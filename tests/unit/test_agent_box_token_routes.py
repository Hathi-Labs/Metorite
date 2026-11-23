"""WS-55 EA-19c — the box-token routes: check, mint, rotate and revoke all.

Spec: ``project-docs/specs/external_agent_access.md`` §10.9 (questions 2, 3,
4 and 5) and the EA-19c row of §15 · D-EA-5 · Metorite task 1840.

**What this file proves.** A member makes a box token only through two
steps: ``POST /agent-access/box-tokens/check`` gives a one-time ticket in
the namespace ``box-mint``, and ``POST /agent-access/box-tokens`` spends it.
The ticket binds the member, the organization and each field of the body.
A rotation has the same controls and also binds the connection id. Only a
person reaches the routes, and the mint answers the raw token once. While
``agent_box_tokens_enabled`` is off, the three routes answer 404, but the
list, the revoke of one connection and "Revoke all box tokens" stay live.
The list carries ``credential``, ``last_seen_addr`` and ``addr_changed_at``,
and writes the audit row ``box_token.new_address`` for the newest mark of a live box, once.
Change access refuses a box.

The routes run on the REAL gateway app (``gateway.main.app``) through
``httpx.ASGITransport``, on a real database, as a NON-privileged role, on a
phase-4-promoted catalog of its own (R8). A minted token runs through the
REAL edge app. The tickets live in a fake Redis under the REAL tenant
wrapper.

**The named mutations of §10.9 (R7).** Each one turned the named test red
on 2026-11-07. ``G`` is ``apps/services/gateway/gateway/routes`` and ``A``
is ``packages/acb_auth/acb_auth``.

* R1. A mint without the ticket: ``G/agent_access.py`` ``mint_box_token``
  skips ``_take_ticket``. ``TestTheTicket::test_a_mint_without_a_ticket_is_400``
  and ``test_a_replayed_ticket_is_400``.
* R2. A mint without ``sameOrigin``, and R7, a rotate without it. The
  gateway sees only the server call of the BFF (spec §10.5), so the check
  is in ``box-tokens/relay.ts``. The red tests are in
  ``workbench/control_plane/src/app/api/agent-access/box-tokens/relay.test.ts``.
* R3. A mint route inside ``MCP_ROUTES``: add
  ``("POST", "/agent-access/box-tokens")`` to ``A/agent_oauth.py``
  ``MCP_ROUTES``. ``TestOnlyAPerson::test_no_box_route_is_in_the_mcp_allowlist``.
* R4. A scope outside the ceiling: ``_box_scopes`` drops an unknown scope
  in silence, or skips the writes flag.
  ``TestTheScopes::test_an_unknown_scope_is_422_and_writes_nothing`` and
  ``test_a_write_scope_with_writes_off_is_422``.
* R5. A mint for another member: ``BoxMintFields`` without
  ``extra="forbid"``. ``TestOnlyAPerson::test_a_body_that_names_a_member_is_422``.
  The member of the row is the session's in
  ``test_the_member_of_the_box_is_the_session``.
* R6. A rotate without the ticket: ``rotate_my_box_token`` skips
  ``_take_ticket``. ``TestTheRotation::test_a_rotate_without_a_ticket_is_400``.
* R8. A rotate of another member's box: ``_own_box`` without the member,
  or ``A/agent_tokens.py`` ``_LOCK_BOX_SQL`` without
  ``member_email``. ``TestTheRotation::test_another_members_box_is_404``.
* R9. A revoke-all that 404s with the flag off: add
  ``Depends(_require_box_tokens)`` to ``revoke_all_box_tokens``.
  ``TestTheFlag::test_flag_off_the_list_the_revoke_and_revoke_all_stay_live``.

More mutations of this slice:

* R10. Change access reads a box: ``_LOCK_MEMBER_CONNECTION_SQL`` and
  ``_CHANGE_GRANT_SQL`` without ``credential <> 'box'``.
  ``TestChangeAccessRefusesABox``. Each predicate alone still gives 404, so
  ``test_change_access_names_the_credential`` holds each one as source.
* R11. A second audit row for one mark: ``audit_new_addresses`` skips the
  ``done`` check. ``TestTheList::test_the_new_address_row_is_written_once``.
* R12. A ticket of the consent namespace: ``_BOX_TICKET_NAMESPACE`` set to
  ``agent-consent``. ``TestTheTicket::test_a_consent_ticket_opens_no_box``.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_box_token_routes.py -v -rs
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

import acb_audit
import structlog
from acb_auth import (
    AgentScope,
    EffectiveAccess,
    UserContext,
    UserRole,
    agent_oauth,
    agent_signing,
    agent_tokens,
)
from acb_auth import access as access_module
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings, tenant_redis
from acb_common.tenant_redis import TenantRedis, organization_scope
from agent_edge import app as edge_app_module
from agent_edge import gateway_client
from fastapi import HTTPException
from gateway.routes import agent_access
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

_DB_GATE = w._DB_GATE
KEY = w.KEY
MEMBER = w.MEMBER
INTERNAL = w.INTERNAL
RESOURCE = w.RESOURCE
R8_VIA = w.R8_VIA
OTHER = "other-routes@mcpwrites.test"
ADMIN = "admin-routes@mcpwrites.test"
B_MEMBER = "b-routes@mcpwrites.test"

_ROOT = Path(__file__).resolve().parents[2]
_ACCESS_FILE = _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "agent_access.py"

CHECK = "/agent-access/box-tokens/check"
MINT = "/agent-access/box-tokens"
REVOKE_ALL = "/agent-access/box-tokens/revoke-all"
LIST = "/agent-access/connections"
ADMIN_LIST = "/integrations/agent-connections"

#: The four routes of EA-19c, as branch 0 names a pair.
BOX_PAIRS = (
    ("POST", "/agent-access/box-tokens/check"),
    ("POST", "/agent-access/box-tokens"),
    ("POST", "/agent-access/box-tokens/{connection_id}/rotate"),
    ("POST", "/agent-access/box-tokens/revoke-all"),
)

READ = agent_oauth.PROJECTS_READ
WRITE = agent_oauth.PROJECTS_WRITE


def _rotate_path(cid: str) -> str:
    return f"/agent-access/box-tokens/{cid}/rotate"


# ═══════════════════════════════════════════════════════════════════════════
# Database-free: the allowlist, the scopes and the source
# ═══════════════════════════════════════════════════════════════════════════


class TestOnlyAPersonBySource:
    def test_no_box_route_is_in_the_mcp_allowlist(self):
        """R3. Branch 0 admits only listed pairs, so an agent cannot mint."""
        for pair in BOX_PAIRS:
            assert pair not in agent_oauth.MCP_ROUTES, pair
            assert pair not in agent_oauth.MCP_WRITE_ROUTES, pair
        assert not [p for p in agent_oauth.MCP_ROUTES if "box-tokens" in p[1]]

    def test_the_mint_takes_the_member_from_the_session(self):
        """R5, as source. The first argument is the session's member, and
        no keyword names an identity."""
        tree = ast.parse(_ACCESS_FILE.read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and ast.unparse(n.func) == "agent_tokens.create_box_connection"]
        assert len(calls) == 1
        assert ast.unparse(calls[0].args[0]) == "email"
        assert not {k.arg for k in calls[0].keywords} & {
            "organization_id", "org", "member_email", "member"}
        handler = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                       and n.name == "mint_box_token")
        assert "email = _box_person(user)" in ast.unparse(handler)

    def test_the_bodies_forbid_extras_and_name_no_identity(self):
        for model in (agent_access.BoxMintFields, agent_access.BoxMintRequest,
                      agent_access.BoxRotateCheck, agent_access.BoxRotateRequest):
            assert model.model_config.get("extra") == "forbid", model.__name__
            assert not set(model.model_fields) & {
                "organization_id", "org", "member", "member_email", "email", "user"}

    async def test_a_signed_agent_gets_404(self, monkeypatch):
        """The second lock: a user with ``agent`` set never reaches the act."""
        monkeypatch.setattr(agent_access, "current_tenant", lambda: str(uuid.uuid4()))
        user = UserContext(
            email=MEMBER, role=UserRole.EMPLOYEE, organization_id=str(uuid.uuid4()),
            access=EffectiveAccess(is_active=True),
            agent=AgentScope(connection_id=str(uuid.uuid4())),
        )
        for call in (lambda: agent_access.check_box_token(None, user),
                     lambda: agent_access.mint_box_token(None, user),
                     lambda: agent_access.rotate_my_box_token(uuid.uuid4(), None, user),
                     lambda: agent_access.revoke_all_box_tokens(user)):
            with pytest.raises(HTTPException) as caught:
                await call()
            assert caught.value.status_code == 404

    def test_change_access_names_the_credential(self):
        """R10, as source: both statements of a change refuse a box."""
        for sql in (agent_tokens._LOCK_MEMBER_CONNECTION_SQL,
                    agent_tokens._CHANGE_GRANT_SQL):
            assert "c.credential <> 'box'" in " ".join(sql.split())

    def test_the_two_namespaces_differ(self):
        assert agent_access._BOX_TICKET_NAMESPACE == "box-mint"
        assert agent_access._BOX_TICKET_NAMESPACE != agent_access._TICKET_NAMESPACE


class TestTheScopesBySource:
    @pytest.mark.parametrize("scope", agent_oauth.SUPPORTED_SCOPES)
    def test_parity_with_the_consent_validator(self, scope, monkeypatch):
        """Spec §10.9 q. 2: each scope that the mint accepts is one that
        the consent validator accepts, less ``offline_access``."""
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", True)
        consent = tuple(s for s in agent_oauth.parse_scope(scope)
                        if s != agent_oauth.OFFLINE_ACCESS)
        if consent:
            assert agent_access._box_scopes([scope]) == consent
        else:
            with pytest.raises(HTTPException) as caught:
                agent_access._box_scopes([scope])
            assert caught.value.detail["error"] == "no_scope"

    @pytest.mark.parametrize("asked", [["admin:all"], ["projects:read", "bogus"],
                                       ['bad"token'], []])
    def test_a_scope_outside_the_ceiling_is_refused(self, asked, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", True)
        with pytest.raises(HTTPException) as caught:
            agent_access._box_scopes(asked)
        assert caught.value.status_code == 422

    def test_offline_access_drops_out(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", True)
        assert agent_access._box_scopes([READ, agent_oauth.OFFLINE_ACCESS]) == (READ,)

    @pytest.mark.parametrize("name", ["", "   ", "a\x00b", "tab\there", "c1\x85x",
                                      "x" * 101])
    def test_a_bad_name_is_refused(self, name):
        with pytest.raises(ValueError):
            agent_access._box_name_ok(name)

    def test_the_new_address_id_is_one_for_each_mark(self):
        cid = str(uuid.uuid4())
        at = datetime(2026, 11, 7, 1, 2, 3, 456789, tzinfo=UTC)
        one = agent_tokens.box_address_event_id(cid, at)
        assert one == agent_tokens.box_address_event_id(cid.upper(), at)
        assert one != agent_tokens.box_address_event_id(cid, at + timedelta(hours=1))
        assert one != agent_tokens.box_address_event_id(str(uuid.uuid4()), at)
        assert str(uuid.UUID(one)) == one


# ═══════════════════════════════════════════════════════════════════════════
# R8: a catalog of its own
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_boxroutes"
_APP_PW = "boxroutes_scratch_pw"


class _FakeRedis(tw._FakeRedis):
    """The fake of the EA-6d suites, with ``SETEX`` and ``GETDEL``."""

    async def setex(self, name: str, seconds: int, value: str) -> bool:
        self._check("setex", name)
        self.store[name] = (value, self.now + seconds)
        return True

    async def getdel(self, name: str) -> str | None:
        self._check("getdel", name)
        found = self._live(name)
        self.store.pop(name, None)
        return found[0] if found else None


@pytest.fixture(scope="module")
def world():
    """A promoted catalog. Org A: the member, another member, an admin, a
    team project, a hidden project and an OAuth connection. Org B: one
    member."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_boxroutes"
    w._drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(w._GENERATED, phase),
                                       encoding="utf-8") as fh:
            w._exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "boxroutes-a"), (org_b, "boxroutes-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org)
    a.member(MEMBER, ["feature:projects"])
    a.member(OTHER, ["feature:projects"])
    a.member(ADMIN, ["feature:projects", "feature:integrations", "integrations:manage"])
    team = a.project("Team")
    hidden = str(a.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses) VALUES ('Hidden', 'active', 'manual', :a,"
        " CAST(:o AS uuid), 'Asia/Kolkata', true) RETURNING id", a=w.OWNER, o=org))
    a.one("INSERT INTO pm_project_grants (project_id, subject, created_by, organization_id)"
          " VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid)) RETURNING project_id",
          p=hidden, s=OTHER, a=w.OWNER, o=org)
    b = w._Seeder(eng, org_b)
    b.member(B_MEMBER, ["feature:projects"])
    world_ = SimpleNamespace(admin=eng, org=org, org_b=org_b, seeder=a, team=team,
                             hidden=hidden, c={}, tok={})
    world_.c["oauth"], world_.tok["oauth"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


@pytest.fixture
def redis_fake(monkeypatch) -> _FakeRedis:
    """The tickets of the gateway and the nonces of branch 0, on one fake."""
    fake = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    monkeypatch.setattr(agent_access, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def audits(monkeypatch) -> list[Any]:
    """Each audit event that a route records, in order."""
    seen: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", seen.append)
    return seen


@pytest.fixture
def live(monkeypatch, world, redis_fake, audits):
    """The real gateway app, with agent access, writes and box tokens on.

    Each test starts with no live box connection, so the caps of one test
    never meet the boxes of another.
    """
    from gateway.main import app as gateway_app

    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_access_orgs", "*"), ("agent_box_tokens_enabled", True),
            ("agent_change_access_orgs", "*"),
            ("agent_edge_signing_key", KEY), ("agent_edge_signing_key_previous", ""),
            ("agent_issuer", w.ISSUER), ("agent_resource", RESOURCE),
            ("agent_edge_gateway_url", "http://gateway.test")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "mcpwrites.test")
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    with world.admin.begin() as c:
        c.execute(text("UPDATE agent_connection SET revoked_at = now(),"
                       " revoked_reason = 'admin' WHERE credential = 'box'"
                       " AND revoked_at IS NULL"))
    world.redis = redis_fake
    world.audits = audits
    with gateway_client.use_transport(httpx.ASGITransport(app=gateway_app)):
        try:
            yield world
        finally:
            invalidate_access()


def _box_flag(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(get_settings(), "agent_box_tokens_enabled", on)


async def _in_scope(world, work) -> Any:
    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _as(world, email: str, method: str, path: str, body: Any = None, *,
              raw: bytes | None = None) -> httpx.Response:
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


async def _signed(world, method: str, path: str, body: Any = None) -> httpx.Response:
    """One call signed for the OAuth connection, as the edge makes it (branch 0)."""
    from gateway.main import app as gateway_app

    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    headers = agent_signing.signed_headers(
        KEY, organization_id=world.org, connection_id=world.c["oauth"],
        actor_via=R8_VIA, method=method, path=path, query="", body=raw)
    headers["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, content=raw, headers=headers)
    return await _in_scope(world, go)


def _box(name: str = "metorite-home", **kw: Any) -> dict[str, Any]:
    return {"name": name, "scopes": [READ, WRITE], "days": 90, **kw}


async def _ticket(world, body: dict[str, Any], email: str = MEMBER) -> str:
    r = await _as(world, email, "POST", CHECK, body)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()["ticket"]


async def _mint(world, body: dict[str, Any] | None = None,
                email: str = MEMBER) -> dict[str, Any]:
    body = body or _box()
    ticket = await _ticket(world, body, email)
    r = await _as(world, email, "POST", MINT, {**body, "ticket": ticket})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


async def _rotate(world, cid: str, email: str = MEMBER, **kw: Any) -> httpx.Response:
    fields = {"days": 90, "now": False, **kw}
    ticket = await _ticket(world, {"connection_id": cid, **fields}, email)
    return await _as(world, email, "POST", _rotate_path(cid), {**fields, "ticket": ticket})


def _admin(world, sql: str, **params) -> Any:
    with world.admin.begin() as c:
        return c.execute(text(sql), params).first()


def _conn(world, cid: str) -> Any:
    return _admin(world, "SELECT * FROM agent_connection WHERE id = CAST(:i AS uuid)", i=cid)


def _boxes(world, member: str | None = None) -> int:
    return int(_admin(
        world, "SELECT count(*) FROM agent_connection WHERE credential = 'box'"
               " AND revoked_at IS NULL AND (CAST(:m AS text) IS NULL OR member_email ="
               " CAST(:m AS text))", m=member)[0])


def _live_tokens(world, cid: str) -> int:
    return int(_admin(world, "SELECT count(*) FROM agent_token WHERE connection_id ="
                             " CAST(:i AS uuid) AND revoked_at IS NULL AND expires_at > now()",
                      i=cid)[0])


async def _edge(world, token: str) -> httpx.Response:
    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                base_url=w.ISSUER) as c:
            return await c.post("/mcp", content=json.dumps(w._rpc("tools/list", {})),
                                headers={"authorization": f"Bearer {token}",
                                         "content-type": "application/json",
                                         "mcp-protocol-version": "2025-11-25"})
    return await _in_scope(world, go)


def _plant(world, binding: Any, namespace: str, org: str) -> str:
    """A ticket written as the check writes one, with no check."""
    ticket = f"planted-{uuid.uuid4().hex}"
    with organization_scope(org):
        name = agent_access._ticket_key(ticket, namespace).value
    world.redis.store[name] = (json.dumps(list(binding)), world.redis.now + 300)
    return ticket


# ── The mint ────────────────────────────────────────────────────────────────


@_DB_GATE
class TestTheMint:
    async def test_a_mint_makes_a_box_that_works_at_the_edge(self, live):
        made = await _mint(live, _box("works", days=30))
        assert made["token"].startswith("mbt_") and len(made["token"]) == 47
        assert made["scopes"] == [READ, WRITE] and made["name"] == "works"
        row = _conn(live, made["connection_id"])
        assert (row.credential, row.member_email, str(row.organization_id)) == (
            "box", MEMBER, live.org)
        assert (row.expires_at - row.created_at).days == 30
        r = await _edge(live, made["token"])
        assert r.status_code == 200, r.text

    async def test_the_member_of_the_box_is_the_session(self, live):
        """R5: the member comes from the session only."""
        made = await _mint(live, _box("mine"), email=OTHER)
        assert _conn(live, made["connection_id"]).member_email == OTHER

    async def test_a_mint_with_reach(self, live):
        made = await _mint(live, _box("reach", pinned_projects=[live.team],
                                      include_personal=True))
        row = _conn(live, made["connection_id"])
        assert [str(p) for p in row.pinned_projects] == [live.team]
        assert row.include_personal is True

    async def test_a_reach_the_member_does_not_see_is_422(self, live):
        for body in (_box(pinned_projects=[live.hidden]),
                     _box(left_out_projects=[str(uuid.uuid4())])):
            r = await _as(live, MEMBER, "POST", CHECK, body)
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["error"] == "project_not_seen"
        both = await _as(live, MEMBER, "POST", CHECK, _box(
            pinned_projects=[live.team], left_out_projects=[live.team]))
        assert both.json()["detail"]["error"] == "project_in_both_lists"
        over = await _as(live, MEMBER, "POST", CHECK, _box(
            pinned_projects=[str(uuid.uuid4()) for _ in range(51)]))
        assert over.status_code == 422

    async def test_the_name_and_the_body_limits(self, live):
        for name in ("", "  ", "a\x01b", "x" * 101):
            r = await _as(live, MEMBER, "POST", CHECK, _box(name))
            assert r.status_code == 422, name
        r = await _as(live, MEMBER, "POST", CHECK, raw=b"{" + b" " * 20_000 + b"}")
        assert r.status_code == 413
        r = await _as(live, MEMBER, "POST", CHECK, _box(days=366))
        assert r.status_code == 422
        r = await _as(live, MEMBER, "POST", CHECK, _box(days="90"))
        assert r.status_code == 422
        assert _boxes(live) == 0

    async def test_an_org_off_the_list_is_403(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_orgs", live.org_b)
        r = await _as(live, MEMBER, "POST", CHECK, _box())
        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "org_not_enabled"

    async def test_the_member_cap_is_409(self, live, monkeypatch):
        monkeypatch.setattr(agent_tokens, "BOX_MEMBER_CAP", 1)
        await _mint(live, _box("one"))
        body = _box("two")
        ticket = await _ticket(live, body)
        r = await _as(live, MEMBER, "POST", MINT, {**body, "ticket": ticket})
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == agent_tokens.BOX_MEMBER_FULL


# ── The ticket ──────────────────────────────────────────────────────────────


@_DB_GATE
class TestTheTicket:
    async def test_a_mint_without_a_ticket_is_400(self, live):
        """R1."""
        r = await _as(live, MEMBER, "POST", MINT, _box())
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["error"] == "invalid_request"
        r = await _as(live, MEMBER, "POST", MINT, {**_box(), "ticket": "made-up"})
        assert r.status_code == 400
        assert _boxes(live) == 0

    async def test_a_replayed_ticket_is_400(self, live):
        body = _box("replay")
        ticket = await _ticket(live, body)
        first = await _as(live, MEMBER, "POST", MINT, {**body, "ticket": ticket})
        assert first.status_code == 200, first.text
        again = await _as(live, MEMBER, "POST", MINT, {**body, "ticket": ticket})
        assert again.status_code == 400
        assert _boxes(live, MEMBER) == 1

    async def test_the_ticket_lives_300_seconds(self, live):
        body = _box("late")
        ticket = await _ticket(live, body)
        live.redis.now += 301
        r = await _as(live, MEMBER, "POST", MINT, {**body, "ticket": ticket})
        assert r.status_code == 400

    async def test_a_ticket_of_another_member_is_400_and_spent(self, live):
        body = _box("theirs")
        ticket = await _ticket(live, body, OTHER)
        r = await _as(live, MEMBER, "POST", MINT, {**body, "ticket": ticket})
        assert r.status_code == 400
        # The spend comes first, so the ticket is gone for its owner too.
        r = await _as(live, OTHER, "POST", MINT, {**body, "ticket": ticket})
        assert r.status_code == 400
        assert _boxes(live) == 0

    async def test_a_ticket_of_another_org_is_400(self, live):
        body = _box("org-b")
        ticket = await _ticket(live, body, B_MEMBER)
        r = await _as(live, MEMBER, "POST", MINT, {**body, "ticket": ticket})
        assert r.status_code == 400
        assert _boxes(live) == 0

    @pytest.mark.parametrize("change", [
        {"name": "another-name"},
        {"scopes": [READ]},
        {"days": 30},
        {"include_personal": True},
        {"pinned_projects": "TEAM"},
    ])
    async def test_a_body_that_differs_from_the_ticket_is_400(self, live, change):
        body = _box("bound")
        ticket = await _ticket(live, body)
        if change.get("pinned_projects") == "TEAM":
            change = {"pinned_projects": [live.team]}
        r = await _as(live, MEMBER, "POST", MINT, {**body, **change, "ticket": ticket})
        assert r.status_code == 400, r.text
        assert _boxes(live) == 0

    async def test_a_consent_ticket_opens_no_box(self, live):
        """R12: the same binding in the consent namespace is no box ticket."""
        body = _box("consent")
        req = agent_access.BoxMintFields(**body)
        binding = agent_access._mint_binding(req, MEMBER, live.org)
        consent = _plant(live, binding, agent_access._TICKET_NAMESPACE, live.org)
        r = await _as(live, MEMBER, "POST", MINT, {**body, "ticket": consent})
        assert r.status_code == 400
        box = _plant(live, binding, agent_access._BOX_TICKET_NAMESPACE, live.org)
        r = await _as(live, MEMBER, "POST", MINT, {**body, "ticket": box})
        assert r.status_code == 200, r.text

    async def test_a_rotate_ticket_opens_no_mint(self, live):
        made = await _mint(live, _box("rot-for-mint"))
        ticket = await _ticket(live, {"connection_id": made["connection_id"], "days": 90})
        r = await _as(live, MEMBER, "POST", MINT, {**_box(), "ticket": ticket})
        assert r.status_code == 400

    async def test_a_redis_outage_is_503(self, live):
        live.redis.fail = ConnectionError("down")
        try:
            r = await _as(live, MEMBER, "POST", CHECK, _box())
        finally:
            live.redis.fail = None
        assert r.status_code == 503
        assert r.headers.get("retry-after") == "2"


# ── The scopes ──────────────────────────────────────────────────────────────


@_DB_GATE
class TestTheScopes:
    async def test_an_unknown_scope_is_422_and_writes_nothing(self, live):
        """R4: the check refuses, and so does a mint whose ticket an older
        check planted for the same body."""
        body = _box(scopes=[READ, "admin:all"])
        r = await _as(live, MEMBER, "POST", CHECK, body)
        assert r.status_code == 422
        assert r.json()["detail"]["scopes"] == ["admin:all"]
        req = agent_access.BoxMintFields(**body)
        ticket = _plant(live, agent_access._mint_binding(req, MEMBER, live.org),
                        agent_access._BOX_TICKET_NAMESPACE, live.org)
        r = await _as(live, MEMBER, "POST", MINT, {**body, "ticket": ticket})
        assert r.status_code == 422
        assert _boxes(live) == 0

    async def test_a_write_scope_with_writes_off_is_422(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        r = await _as(live, MEMBER, "POST", CHECK, _box())
        assert r.status_code == 422
        assert r.json()["detail"]["error"] == agent_oauth.WRITES_DISABLED_CODE
        made = await _mint(live, _box("read-only", scopes=[READ]))
        assert made["scopes"] == [READ]

    async def test_offline_access_is_dropped(self, live):
        made = await _mint(live, _box("no-offline", scopes=[READ, "offline_access"]))
        assert made["scopes"] == [READ]
        assert list(_conn(live, made["connection_id"]).scopes) == [READ]


# ── Only a person ───────────────────────────────────────────────────────────


@_DB_GATE
class TestOnlyAPerson:
    async def test_no_box_route_is_in_the_mcp_allowlist(self):
        """R3, here too, so the R8 class names it."""
        for pair in BOX_PAIRS:
            assert pair not in agent_oauth.MCP_ROUTES

    async def test_an_agent_connection_cannot_reach_a_box_route(self, live):
        """Branch 0 refuses each pair outside ``MCP_ROUTES`` (401, reason
        ``method``)."""
        made = await _mint(live, _box("for-rotate"))
        for path, body in ((CHECK, _box("agent")), (MINT, {**_box("agent"), "ticket": "x"}),
                           (_rotate_path(made["connection_id"]), {"ticket": "x"}),
                           (REVOKE_ALL, None)):
            r = await _signed(live, "POST", path, body)
            assert r.status_code in (401, 403, 404), (path, r.status_code, r.text)
        assert _boxes(live) == 1
        assert _live_tokens(live, made["connection_id"]) == 1

    async def test_a_body_that_names_a_member_is_422(self, live):
        """R5: an identity in the body is refused, never read."""
        for extra in ({"member_email": OTHER}, {"organization_id": live.org_b}):
            r = await _as(live, MEMBER, "POST", CHECK, {**_box(), **extra})
            assert r.status_code == 422, extra
        body = _box("named")
        ticket = await _ticket(live, body)
        r = await _as(live, MEMBER, "POST", MINT,
                      {**body, "ticket": ticket, "member_email": OTHER})
        assert r.status_code == 422
        assert _boxes(live) == 0

    async def test_an_unknown_member_is_refused(self, live):
        r = await _as(live, "nobody@mcpwrites.test", "POST", CHECK, _box())
        assert r.status_code in (401, 403)


# ── The rotation ────────────────────────────────────────────────────────────


@_DB_GATE
class TestTheRotation:
    async def test_a_rotation_gives_a_new_token_and_keeps_the_old_a_day(self, live):
        made = await _mint(live, _box("rotate"))
        r = await _rotate(live, made["connection_id"], days=30)
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        new = r.json()
        assert new["token"] != made["token"] and new["name"] == "rotate"
        assert new["connection_id"] == made["connection_id"]
        assert _live_tokens(live, made["connection_id"]) == 2
        assert (await _edge(live, new["token"])).status_code == 200

    async def test_rotate_now_kills_the_old_token(self, live):
        made = await _mint(live, _box("rotate-now"))
        r = await _rotate(live, made["connection_id"], now=True)
        assert r.status_code == 200, r.text
        assert _live_tokens(live, made["connection_id"]) == 1
        assert (await _edge(live, made["token"])).status_code == 401

    async def test_a_rotate_without_a_ticket_is_400(self, live):
        """R6."""
        made = await _mint(live, _box("rotate-no-ticket"))
        r = await _as(live, MEMBER, "POST", _rotate_path(made["connection_id"]),
                      {"days": 90, "now": True})
        assert r.status_code == 400
        assert _live_tokens(live, made["connection_id"]) == 1

    async def test_a_ticket_of_another_box_or_other_days_is_400(self, live):
        one = await _mint(live, _box("rot-a"))
        two = await _mint(live, _box("rot-b"))
        ticket = await _ticket(live, {"connection_id": one["connection_id"], "days": 90})
        r = await _as(live, MEMBER, "POST", _rotate_path(two["connection_id"]),
                      {"days": 90, "ticket": ticket})
        assert r.status_code == 400
        ticket = await _ticket(live, {"connection_id": one["connection_id"], "days": 90})
        r = await _as(live, MEMBER, "POST", _rotate_path(one["connection_id"]),
                      {"days": 90, "now": True, "ticket": ticket})
        assert r.status_code == 400
        assert _live_tokens(live, one["connection_id"]) == 1

    async def test_another_members_box_is_404(self, live):
        """R8: the check and the store read the member's own rows only."""
        theirs = await _mint(live, _box("theirs"), email=OTHER)
        cid = theirs["connection_id"]
        r = await _as(live, MEMBER, "POST", CHECK, {"connection_id": cid, "days": 90})
        assert r.status_code == 404
        req = agent_access.BoxRotateFields(days=90, now=True)
        ticket = _plant(live, agent_access._rotate_binding(cid, req, MEMBER, live.org),
                        agent_access._BOX_TICKET_NAMESPACE, live.org)
        r = await _as(live, MEMBER, "POST", _rotate_path(cid),
                      {"days": 90, "now": True, "ticket": ticket})
        assert r.status_code == 404
        assert _live_tokens(live, cid) == 1
        assert (await _edge(live, theirs["token"])).status_code == 200

    async def test_an_oauth_connection_is_404(self, live):
        cid = live.c["oauth"]
        r = await _as(live, MEMBER, "POST", CHECK, {"connection_id": cid, "days": 90})
        assert r.status_code == 404


# ── The flag and revoke all ─────────────────────────────────────────────────


@_DB_GATE
class TestTheFlag:
    async def test_flag_off_check_mint_and_rotate_are_404(self, live, monkeypatch):
        made = await _mint(live, _box("flag"))
        body = _box("flag-off")
        ticket = await _ticket(live, body)
        _box_flag(monkeypatch, False)
        for path, sent in ((CHECK, body), (MINT, {**body, "ticket": ticket}),
                           (_rotate_path(made["connection_id"]), {"ticket": ticket}),
                           (_rotate_path("not-a-uuid"), None), (CHECK, None)):
            r = await _as(live, MEMBER, "POST", path, sent)
            assert r.status_code == 404, (path, r.text)
        assert _boxes(live) == 1

    async def test_flag_off_the_list_the_revoke_and_revoke_all_stay_live(
            self, live, monkeypatch):
        """R9: an admin can still remove access while the flag is off."""
        one = await _mint(live, _box("keep-one"))
        two = await _mint(live, _box("keep-two"), email=OTHER)
        three = await _mint(live, _box("keep-three"))
        _box_flag(monkeypatch, False)
        listed = await _as(live, MEMBER, "GET", LIST)
        assert listed.status_code == 200
        assert one["connection_id"] in {r["id"] for r in listed.json()["connections"]}
        gone = await _as(live, MEMBER, "DELETE", f"{LIST}/{three['connection_id']}")
        assert gone.status_code == 204
        assert _conn(live, three["connection_id"]).revoked_reason == "member"
        r = await _as(live, ADMIN, "POST", REVOKE_ALL)
        assert r.status_code == 200, r.text
        assert r.json() == {"revoked": 2}
        for made in (one, two):
            row = _conn(live, made["connection_id"])
            assert row.revoked_at is not None and row.revoked_reason == "flag_off"
            assert _live_tokens(live, made["connection_id"]) == 0
        # The OAuth connection stays.
        assert _conn(live, live.c["oauth"]).revoked_at is None
        # A flip back on revives nothing.
        _box_flag(monkeypatch, True)
        assert (await _edge(live, one["token"])).status_code == 401

    async def test_revoke_all_is_for_an_admin(self, live):
        made = await _mint(live, _box("not-admin"))
        r = await _as(live, MEMBER, "POST", REVOKE_ALL)
        assert r.status_code == 403
        assert _conn(live, made["connection_id"]).revoked_at is None

    async def test_revoke_all_touches_one_organization(self, live):
        mine = await _mint(live, _box("org-a"))
        theirs = await _mint(live, _box("org-b"), email=B_MEMBER)
        r = await _as(live, ADMIN, "POST", REVOKE_ALL)
        assert r.json() == {"revoked": 1}
        assert _conn(live, mine["connection_id"]).revoked_reason == "flag_off"
        assert _conn(live, theirs["connection_id"]).revoked_at is None

    async def test_revoke_all_writes_the_audit_rows(self, live):
        made = await _mint(live, _box("audited"))
        live.audits.clear()
        await _as(live, ADMIN, "POST", REVOKE_ALL)
        actions = [(e.action, e.target) for e in live.audits]
        assert (agent_access.REVOKE_ACTION, f"agent_connection:{made['connection_id']}") in actions
        assert any(a == agent_access.BOX_REVOKE_ALL_ACTION for a, _ in actions)


# ── The list and the new-address row ───────────────────────────────────────


def _persisting(world) -> tuple[list[Any], Any]:
    """Make ``acb_audit.record`` write each row into ``audit_event``, as the
    real writer does, with the tenant of the event bound. A second row of
    the same id is a conflict, as the primary key makes it."""
    seen: list[Any] = []

    def record(event: Any) -> None:
        seen.append(event)
        with world.admin.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                      {"t": event.organization_id})
            c.execute(text(
                "INSERT INTO audit_event (id, at, actor, action, target, payload)"
                " VALUES (CAST(:i AS uuid), :at, :a, :ac, :t, CAST(:p AS jsonb))"
                " ON CONFLICT (id) DO NOTHING"),
                {"i": str(event.id), "at": event.at, "a": event.actor,
                 "ac": event.action, "t": event.target, "p": json.dumps(event.payload)})
    return seen, record


def _mark(world, cid: str, addr: str, ago_s: int = 5) -> None:
    _admin(world, "UPDATE agent_connection SET last_seen_addr = :a, addr_changed_at ="
                  " now() - make_interval(secs => :s) WHERE id = CAST(:i AS uuid)"
                  " RETURNING id", a=addr, s=ago_s, i=cid)


def _address_rows(world, cid: str) -> int:
    return int(_admin(world, "SELECT count(*) FROM audit_event WHERE action = :a"
                             " AND target = :t", a=agent_tokens.BOX_NEW_ADDRESS_ACTION,
                      t=f"agent_connection:{cid}")[0])


@_DB_GATE
class TestTheList:
    async def test_the_list_shows_the_credential_and_the_address(self, live):
        made = await _mint(live, _box("listed"))
        _mark(live, made["connection_id"], "198.51.100.9")
        for email, path in ((MEMBER, LIST), (ADMIN, ADMIN_LIST)):
            rows = (await _as(live, email, "GET", path)).json()["connections"]
            row = next(r for r in rows if r["id"] == made["connection_id"])
            assert row["credential"] == "box"
            assert row["last_seen_addr"] == "198.51.100.9"
            assert row["addr_changed_at"] is not None
            oauth = next(r for r in rows if r["id"] == live.c["oauth"])
            assert oauth["credential"] == "oauth" and oauth["last_seen_addr"] is None

    async def test_the_new_address_row_is_written_once(self, live, monkeypatch):
        """R11: two list reads and the admin's list write one row for one
        mark. A new mark writes a second row."""
        seen, record = _persisting(live)
        monkeypatch.setattr(acb_audit, "record", record)
        made = await _mint(live, _box("moved"))
        cid = made["connection_id"]
        seen.clear()
        assert (await _as(live, MEMBER, "GET", LIST)).status_code == 200
        assert [e for e in seen if e.action == agent_tokens.BOX_NEW_ADDRESS_ACTION] == []
        _mark(live, cid, "198.51.100.10")
        for email, path in ((MEMBER, LIST), (MEMBER, LIST), (ADMIN, ADMIN_LIST)):
            assert (await _as(live, email, "GET", path)).status_code == 200
        address = [e for e in seen if e.action == agent_tokens.BOX_NEW_ADDRESS_ACTION]
        assert len(address) == 1, address
        assert address[0].target == f"agent_connection:{cid}"
        assert address[0].payload["addr"] == "198.51.100.10"
        assert address[0].organization_id == live.org
        assert _address_rows(live, cid) == 1
        _mark(live, cid, "198.51.100.11", ago_s=1)
        assert (await _as(live, MEMBER, "GET", LIST)).status_code == 200
        assert _address_rows(live, cid) == 2

    async def test_the_row_names_the_address_that_set_the_mark(self, live, monkeypatch):
        """Review of 2026-11-07: a thief at A sets the mark, the real box at B
        calls a minute later, and then a member reads the list. The row and
        the list must name A, not B."""
        seen, record = _persisting(live)
        monkeypatch.setattr(acb_audit, "record", record)
        made = await _mint(live, _box("thief"))
        cid = made["connection_id"]

        def touch(addr: str) -> None:
            _admin(live, "UPDATE agent_connection SET last_used_at = now() - interval '2 minutes'"
                         " WHERE id = CAST(:i AS uuid) RETURNING id", i=cid)
            _admin(live, agent_tokens._TOUCH_BOX_SQL, addr=addr, connection_id=cid, org=live.org)

        touch("198.51.100.1")   # the box: the first address, no mark
        touch("203.0.113.66")   # the thief: a change, the mark
        touch("198.51.100.1")   # the box again, a minute later
        rows = (await _as(live, MEMBER, "GET", LIST)).json()["connections"]
        row = next(r for r in rows if r["id"] == cid)
        assert row["last_seen_addr"] == "203.0.113.66"
        written = [e for e in seen if e.action == agent_tokens.BOX_NEW_ADDRESS_ACTION]
        assert [e.payload["addr"] for e in written] == ["203.0.113.66"]

    async def test_a_failed_audit_read_never_fails_the_list(self, live, monkeypatch):
        made = await _mint(live, _box("audit-down"))
        _mark(live, made["connection_id"], "198.51.100.12")

        async def broken(*a, **k):
            raise RuntimeError("down")

        monkeypatch.setattr(agent_tokens, "audited_events", broken)
        assert (await _as(live, MEMBER, "GET", LIST)).status_code == 200


# ── Change access refuses a box ────────────────────────────────────────────


@_DB_GATE
class TestChangeAccessRefusesABox:
    async def test_a_box_is_404_and_changes_nothing(self, live):
        """R10."""
        made = await _mint(live, _box("no-change"))
        cid = made["connection_id"]
        r = await _as(live, MEMBER, "PATCH", f"{LIST}/{cid}", {
            "scopes": [READ], "pinned_projects": [], "left_out_projects": [],
            "include_personal": False})
        assert r.status_code == 404, r.text
        assert list(_conn(live, cid).scopes) == [READ, WRITE]

    async def test_an_oauth_connection_still_changes(self, live):
        cid = live.c["oauth"]
        r = await _as(live, MEMBER, "PATCH", f"{LIST}/{cid}", {
            "scopes": [READ, WRITE], "pinned_projects": [], "left_out_projects": [],
            "include_personal": True})
        assert r.status_code == 200, r.text


# ── No raw token outside its one answer ─────────────────────────────────────


@_DB_GATE
class TestTheRawTokenStaysInItsAnswer:
    async def test_no_log_audit_or_list_holds_the_token(self, live, caplog):
        caplog.set_level("DEBUG")
        with structlog.testing.capture_logs() as logs:
            made = await _mint(live, _box("quiet"))
            rotated = (await _rotate(live, made["connection_id"])).json()
            listed = (await _as(live, MEMBER, "GET", LIST)).text
            listed += (await _as(live, ADMIN, "GET", ADMIN_LIST)).text
        seen = json.dumps([str(e) for e in logs]) + caplog.text + listed
        seen += json.dumps([str(a) for a in live.audits])
        seen += json.dumps([str(v) for v in live.redis.store.values()])
        for raw in (made["token"], rotated["token"]):
            assert raw not in seen
            assert agent_tokens.hash_token(raw) not in seen
        assert any(e.get("event") == "agent_access.box_token_created" for e in logs)
