"""WS-MCP SW-77 — the box heartbeat and the machine record, on a real Postgres (R8).

Spec: ``project-docs/specs/agent_coordination.md`` §3.11.4 and SW-77, and
``project-docs/specs/external_agent_access.md`` §10.9 (box tokens).

**Hermetic half** (no database): the migration is found by CONTENT (R1) and
has the house shape of R5. The flag reads per org. The edge counts the
runner scope only for a box token. The mint keeps ``box:runner`` alone. The
note loses each control character.

**R8 half.** The edge calls the REAL gateway app through
``httpx.ASGITransport``. Branch 0, the real ``/agent-box-health`` router and
the real ``agent_connection`` rows run on a private, phase-4-promoted
catalog, as a NON-privileged role (no superuser, no BYPASSRLS), with two
organizations. Branch 0 keeps its nonces and its write cap in a fake under the
REAL ``TenantRedis``, so CI needs no Redis. It proves the "Done when" of
SW-77:

1. A box token with ``box:runner`` writes a heartbeat, and a second one
   replaces it (one row for each box).
2. An OAuth connection gets 403 at the gateway (``box_only``), and a tool
   error with no request at the edge. So does a box with no runner scope,
   and a person of the org.
3. A member reads only the boxes of the member's org that the member may
   see: the member's own boxes, or every box of the org with
   ``integrations:manage``. Org B reads nothing of org A.
4. A revoked token writes nothing.

It also proves the 3-minute offline rule, the flag, and the untrusted values.

Mutations (R7), each turns the named test red. ``R`` is
``apps/services/gateway/gateway/routes/agent_box_health.py``, ``D`` is
``packages/acb_auth/acb_auth/deps.py`` and ``E`` is
``apps/services/agent_edge/agent_edge/tools.py``.

* B1. ``D`` ``_claim_call`` AND ``R`` ``_box_caller``: drop the box check (keep
  the scope check). ``test_an_oauth_connection_that_names_the_scope_gets_403``.
* B2. ``R`` ``_LIST_SQL``: drop ``CAST(:all AS BOOLEAN) OR h.member_email = ...``
  (no member filter). ``test_a_member_reads_only_own_boxes``.
* B3. ``R`` ``_LIST_SQL``: drop ``h.organization_id = CAST(:org AS uuid)``
  with a role that bypasses RLS. RLS still holds org B apart for the app role,
  so ``test_org_b_reads_no_box_of_org_a`` stays green, and
  ``test_the_read_names_the_org`` (hermetic) turns red.
* B4. The revoked check: ``acb_auth.agent_tokens`` lookup (``revoked_at IS
  NULL``). ``test_a_revoked_token_writes_nothing``.
* B5. ``R`` ``box_health_allowed``: always True. ``test_the_flag_off_is_404``.
* B6. ``E`` ``_report_box_health``: drop the box check. The edge then sends
  the request, and branch 0 answers 403.
  ``test_an_oauth_connection_gets_a_tool_error_and_sends_nothing``.
* B7. ``R`` ``_box_out``: compute ``state`` from nothing (always online).
  ``test_a_heartbeat_older_than_3_minutes_reads_offline``.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_box_health_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_auth import access as access_module
from acb_auth import agent_oauth, agent_signing, agent_tokens
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings, tenant_redis
from acb_common.tenant_redis import TenantRedis
from agent_edge import app as edge_app_module
from agent_edge import bearer, gateway_client, tools
from fastapi import HTTPException
from gateway.routes import agent_box_health as box_health
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database
from tests.unit.test_mcp_edge_task_writes import _FakeRedis
from tests.unit.test_mcp_edge_writes import _DB_GATE, _URL, _drop_database

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"

ISSUER = "https://mcp.metorite.com"
RESOURCE = "https://mcp.metorite.com/mcp"
KEY = "mcp-box-health-signing-key-" + "b" * 24
INTERNAL = "mcp-box-health-internal-token"
OAUTH_ORIGIN = "https://client.example"
OAUTH_CLIENT = "https://client.example/meta.json"
DOMAIN = "boxhealth.test"
OWNER_A = f"owner-a@{DOMAIN}"
OTHER_A = f"other-a@{DOMAIN}"
ADMIN_A = f"admin-a@{DOMAIN}"
MEMBER_B = f"member-b@{DOMAIN}"

_APP_ROLE = "acb_app_boxhealth"
_APP_PW = "boxhealth_scratch_pw"


# ── Hermetic ────────────────────────────────────────────────────────────────


def _migration() -> str:
    """The migration that creates ``agent_box_health``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("[0-9]*_*.sql"))
        if "CREATE TABLE IF NOT EXISTS agent_box_health" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0].read_text(encoding="utf-8")


def test_the_migration_has_the_house_shape() -> None:
    """R5 and R6: a tenant column with ENABLE and FORCE RLS and one policy,
    one row for each box, no GRANT, expand only."""
    sql = _migration()
    assert re.search(r"FORCE\s+ROW LEVEL SECURITY", sql) and "ENABLE ROW LEVEL SECURITY" in sql
    assert re.search(r"REFERENCES organization \(id\) ON DELETE CASCADE\s+DEFAULT", sql)
    assert "PRIMARY KEY (organization_id, connection_id)" in sql
    assert "REFERENCES agent_connection (id) ON DELETE CASCADE" in sql
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in sql
    assert "GRANT" not in sql.replace("No GRANT", "")
    for word in ("DROP TABLE", "DROP COLUMN", "RENAME", "ALTER TABLE agent_connection"):
        assert word not in sql, word


def test_the_flag_is_per_org(monkeypatch: pytest.MonkeyPatch) -> None:
    org = str(uuid.uuid4())
    monkeypatch.setattr(get_settings(), "agent_box_health_orgs", "")
    assert not box_health.box_health_allowed(org)
    monkeypatch.setattr(get_settings(), "agent_box_health_orgs", f"x, {org.upper()}")
    assert box_health.box_health_allowed(org)
    assert not box_health.box_health_allowed(str(uuid.uuid4()))
    monkeypatch.setattr(get_settings(), "agent_box_health_orgs", "*")
    assert box_health.box_health_allowed(org)
    assert not box_health.box_health_allowed(None)


def test_only_a_box_token_holds_the_runner_scope() -> None:
    """The edge counts ``box:runner`` only for a box, and only when the token
    and the connection both name it. An OAuth token never holds it."""
    runner = agent_oauth.BOX_RUNNER
    assert bearer.effective_scopes(runner, [runner], box=True) == frozenset({runner})
    assert bearer.effective_scopes(runner, [runner], box=False) == frozenset()
    assert bearer.effective_scopes(runner, ["projects:read"], box=True) == frozenset()
    assert bearer.effective_scopes("projects:read", [runner, "projects:read"],
                                   box=True) == frozenset({"projects:read"})
    # Consent and the token endpoint never grant it.
    assert agent_oauth.parse_scope(f"projects:read {runner}") == ("projects:read",)
    assert agent_oauth.parse_box_scope(runner) == (runner,)
    assert agent_oauth.box_scope_string([runner]) == runner
    assert agent_oauth.box_scope_string(["projects:write", "projects:read"]) == (
        "projects:read projects:write")


def test_the_mint_keeps_the_runner_scope_alone() -> None:
    """Q18: the runner token is a second token with ``box:runner`` only."""
    from gateway.routes import agent_access

    assert agent_tokens._box_scopes(["box:runner"]) == ("box:runner",)
    with pytest.raises(ValueError):
        agent_tokens._box_scopes(["box:runner", "projects:read"])
    assert agent_access._box_scopes(["box:runner"]) == ("box:runner",)
    with pytest.raises(HTTPException) as refused:
        agent_access._box_scopes(["projects:read box:runner"])
    assert refused.value.status_code == 422
    assert refused.value.detail["error"] == agent_oauth.BOX_RUNNER_ALONE


def test_the_note_loses_each_control_character() -> None:
    assert box_health.clean_note("ok\x00 until\x1b[31m 14:00\x7f\x85") == "ok until[31m 14:00"
    assert box_health.clean_note("  \x00\n ") is None
    assert box_health.clean_note("x" * 500) == "x" * box_health.NOTE_MAX


def test_the_read_names_the_org() -> None:
    """The read filters on the org in its WHERE, not only through RLS, and on
    the member unless the caller holds the manage permission."""
    sql = box_health._LIST_SQL
    assert "h.organization_id = CAST(:org AS uuid)" in sql
    assert "(CAST(:all AS BOOLEAN) OR h.member_email = CAST(:me AS TEXT))" in sql
    assert "c.revoked_at IS NULL" in sql and "c.expires_at > now()" in sql


# ── R8: the world ───────────────────────────────────────────────────────────

class _Seeder:
    """Admin writes with the tenant GUC set."""

    def __init__(self, engine, org: str) -> None:
        self.engine = engine
        self.org = org

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
        self.one("INSERT INTO org_membership (organization_id, user_id, status)"
                 " VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active') RETURNING user_id",
                 o=self.org, u=str(identity))
        app_user = self.one(
            "INSERT INTO app_user (email, display_name, organization_id, status)"
            " VALUES (:e, :e, CAST(:o AS uuid), 'active') RETURNING id", e=email, o=self.org)
        role = self.one(
            "INSERT INTO org_role (organization_id, slug, display_name)"
            " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id",
            o=self.org, s="box-" + email.split("@")[0])
        for permission in permissions:
            self.one("INSERT INTO org_role_permission (role_id, permission)"
                     " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                     r=str(role), p=permission)
        self.one("INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
                 " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))

    def connection(self, member: str, scopes: tuple[str, ...], *, box: str | None) -> tuple[str, str]:
        """A live connection and its one access token: ``(id, raw token)``.

        *box* is the box name of a connection of credential ``box``, as
        ``agent_tokens.create_box_connection`` writes one. ``None`` gives an
        OAuth connection."""
        client_id = agent_oauth.BOX_CLIENT_ID if box else OAUTH_CLIENT
        cid = str(self.one(
            "INSERT INTO agent_connection (organization_id, member_email, client_id,"
            " client_name, client_origin, scopes, pinned_projects, include_personal,"
            " credential, expires_at) VALUES (CAST(:o AS uuid), :m, :cid, :name, :origin,"
            " CAST(:scopes AS text[]), CAST(:pins AS uuid[]), false, :cred,"
            " now() + interval '1 day') RETURNING id",
            o=self.org, m=member, cid=client_id, name=box or "Client",
            origin=ISSUER if box else OAUTH_ORIGIN, scopes=list(scopes), pins=[],
            cred="box" if box else "oauth"))
        raw = agent_tokens.new_box_token() if box else agent_tokens.new_token()
        self.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
                 " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
                 " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour', :cid, :r, :s)"
                 " RETURNING id",
                 o=self.org, c=cid, h=agent_tokens.hash_token(raw), cid=client_id,
                 r=RESOURCE, s=" ".join(scopes))
        return cid, raw


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, two tenants, members and connections."""
    if not _URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_boxhealth"
    _clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
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
        c.execute(text(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {_APP_ROLE}"))

    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org_a, "boxhealth-a"), (org_b, "boxhealth-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a, b = _Seeder(eng, org_a), _Seeder(eng, org_b)
    a.member(OWNER_A, ["feature:projects"])
    a.member(OTHER_A, ["feature:projects"])
    a.member(ADMIN_A, ["feature:projects", box_health.MANAGE_PERMISSION])
    b.member(MEMBER_B, ["feature:projects"])
    runner = (agent_oauth.BOX_RUNNER,)
    w = SimpleNamespace(admin=eng, org_a=org_a, org_b=org_b, c={}, tok={}, org={})
    for name, seeder, member, scopes, box in (
            ("box_a", a, OWNER_A, runner, "metorite-home"),
            ("box_a_read", a, OWNER_A, ("projects:read",), "metorite-read"),
            # An OAuth row that names the runner scope: the scope must still
            # never count for it.
            ("oauth_a", a, OWNER_A, ("projects:read", "projects:write", *runner), None),
            ("box_other_a", a, OTHER_A, runner, "other-box"),
            ("box_revoked", a, OWNER_A, runner, "revoked-box"),
            ("box_b", b, MEMBER_B, runner, "b-box")):
        w.c[name], w.tok[name] = seeder.connection(member, scopes, box=box)
        w.org[name] = seeder.org
    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield w

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def live(monkeypatch, world):
    """The real gateway app at ``agent_edge_gateway_url``, writes, box tokens
    and the heartbeat on, and no heartbeat row at the start."""
    from gateway.main import app as gateway_app

    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_access_orgs", "*"), ("agent_edge_signing_key", KEY),
            ("agent_edge_signing_key_previous", ""), ("agent_issuer", ISSUER),
            ("agent_resource", RESOURCE), ("agent_edge_gateway_url", "http://gateway.test"),
            ("agent_box_tokens_enabled", True), ("agent_box_health_orgs", "*"),
            ("agent_door_apps", "")):
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    redis = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(redis))
    invalidate_access()
    with world.admin.begin() as c:
        c.execute(text("DELETE FROM agent_box_health"))
        c.execute(text("UPDATE agent_connection SET revoked_at = now()"
                       " WHERE id = CAST(:c AS uuid) AND revoked_at IS NULL"),
                  {"c": world.c["box_revoked"]})
    with gateway_client.use_transport(httpx.ASGITransport(app=gateway_app)):
        try:
            yield world
        finally:
            invalidate_access()


async def _in_scope(world, work) -> Any:
    """Run *work* in its own task, on the suite's non-privileged DSN."""
    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _mcp(world, name: str, method: str, params: dict[str, Any]) -> httpx.Response:
    message = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                base_url=ISSUER) as client:
            return await client.post("/mcp", content=json.dumps(message), headers={
                "authorization": f"Bearer {world.tok[name]}",
                "content-type": "application/json", "mcp-protocol-version": "2025-11-25"})
    return await _in_scope(world, go)


async def _call(world, name: str, **arguments: Any) -> httpx.Response:
    return await _mcp(world, name, "tools/call",
                      {"name": "report_box_health", "arguments": arguments})


def _result(r: httpx.Response) -> dict[str, Any]:
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _ok(r: httpx.Response) -> dict[str, Any]:
    result = _result(r)
    assert result["isError"] is False, result
    return result["structuredContent"]


def _error(r: httpx.Response) -> str:
    result = _result(r)
    assert result["isError"] is True, result
    return result["content"][0]["text"]


def _via(world, name: str) -> str:
    """The stamp that branch 0 expects of the connection *name*."""
    box = not name.startswith("oauth")
    with world.admin.connect() as c:
        row = c.execute(text("SELECT client_origin, client_id, client_name, credential"
                             " FROM agent_connection WHERE id = CAST(:c AS uuid)"),
                        {"c": world.c[name]}).one()
    return agent_oauth.actor_via_for(
        row.client_origin, client_id=row.client_id, credential=row.credential,
        connection_id=world.c[name], client_name=row.client_name) if box else (
        agent_oauth.actor_via_for(row.client_origin, client_id=row.client_id))


async def _signed_post(world, name: str, body: dict[str, Any]) -> httpx.Response:
    """One signed POST of the heartbeat that the test signs itself, straight
    to the gateway app, past the checks of the edge."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode()
    headers = agent_signing.signed_headers(
        KEY, organization_id=world.org[name], connection_id=world.c[name],
        actor_via=_via(world, name), method="POST", path="/agent-box-health",
        query="", body=raw)
    headers["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.post("/agent-box-health", headers=headers, content=raw)
    return await _in_scope(world, go)


async def _as_member(world, who: str, method: str = "GET",
                     body: dict[str, Any] | None = None) -> httpx.Response:
    """One request as the member *who* in the app: the internal bearer and the
    member's email (branch 1a). No ``UserContext.agent`` is set."""
    from gateway.main import app as gateway_app

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(
                method, "/agent-box-health", json=body,
                headers={"Authorization": f"Bearer {INTERNAL}", "X-User-Email": who})
    return await _in_scope(world, go)


def _rows(world) -> list[Any]:
    with world.admin.connect() as c:
        return list(c.execute(text(
            "SELECT CAST(connection_id AS TEXT) AS cid, CAST(organization_id AS TEXT) AS org,"
            " member_email, mem_free_mb, load_1m, sessions, sign_in_state, account_state,"
            " account_note, ci_lane_busy, leases, reported_at FROM agent_box_health"
            " ORDER BY reported_at")))


def _names(r: httpx.Response) -> set[str]:
    assert r.status_code == 200, r.text
    return {box["name"] for box in r.json()["boxes"]}


BEAT = {"mem_free_mb": 4096, "load_1m": 1.25, "sessions": 3, "sign_in": "signed_in",
        "account": "rate_limited", "account_note": "until 14:00", "ci_lane_busy": True,
        "leases": {"database": 2, "port": 1}}


@_DB_GATE
class TestTheBoxWrites:

    async def test_a_box_token_writes_a_heartbeat(self, live):
        out = _ok(await _call(live, "box_a", **BEAT))
        assert out["offline_after_s"] == 180 and out["reported_at"]
        rows = _rows(live)
        assert len(rows) == 1
        row = rows[0]
        assert row.cid == live.c["box_a"] and row.org == live.org_a
        assert row.member_email == OWNER_A
        assert (row.mem_free_mb, float(row.load_1m), row.sessions) == (4096, 1.25, 3)
        assert (row.sign_in_state, row.account_state) == ("signed_in", "rate_limited")
        assert row.account_note == "until 14:00" and row.ci_lane_busy is True
        assert row.leases == {"database": 2, "port": 1}

    async def test_a_second_heartbeat_replaces_the_first(self, live):
        _ok(await _call(live, "box_a", **BEAT))
        first = _rows(live)[0].reported_at
        _ok(await _call(live, "box_a", sessions=5))
        rows = _rows(live)
        assert len(rows) == 1
        assert rows[0].sessions == 5 and rows[0].mem_free_mb is None
        assert rows[0].sign_in_state == "unknown" and rows[0].leases == {}
        assert rows[0].reported_at >= first

    async def test_tools_list_shows_the_tool_to_the_runner_box_only(self, live):
        def names(r: httpx.Response) -> set[str]:
            assert r.status_code == 200, r.text
            return {t["name"] for t in r.json()["result"]["tools"]}

        runner = names(await _mcp(live, "box_a", "tools/list", {}))
        assert runner == {"report_box_health"}
        assert "report_box_health" not in names(await _mcp(live, "oauth_a", "tools/list", {}))
        assert "report_box_health" not in names(await _mcp(live, "box_a_read", "tools/list", {}))

    @pytest.mark.parametrize("tool", ["list_tasks", "whoami"])
    async def test_the_runner_token_reaches_no_other_tool(self, live, tool):
        """``box:runner`` covers its tools and nothing else, not even
        ``whoami``, which any other live token may call."""
        r = await _mcp(live, "box_a", "tools/call", {"name": tool, "arguments": {}})
        assert r.status_code == 403
        assert 'scope="projects:read"' in r.headers.get("www-authenticate", "")


@_DB_GATE
class TestWhoMayNotWrite:

    async def test_an_oauth_connection_that_names_the_scope_gets_403(self, live):
        """The gateway half: a signed POST of an OAuth connection whose row
        names ``box:runner`` gets 403 ``box_only`` and writes nothing."""
        r = await _signed_post(live, "oauth_a", BEAT)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == agent_oauth.BOX_ONLY_CODE
        assert _rows(live) == []

    async def test_an_oauth_connection_gets_a_tool_error_and_sends_nothing(
            self, live, monkeypatch):
        sent: list[Any] = []
        real = gateway_client.write

        async def spy(*args: Any, **kwargs: Any) -> Any:
            sent.append(args)
            return await real(*args, **kwargs)

        monkeypatch.setattr(tools, "gateway_write", spy)
        assert _error(await _call(live, "oauth_a", **BEAT)) == tools.TEXT_BOX_ONLY
        assert sent == [] and _rows(live) == []

    async def test_a_box_with_no_runner_scope_gets_403(self, live):
        r = await _call(live, "box_a_read", **BEAT)
        assert r.status_code == 403
        assert 'scope="box:runner"' in r.headers.get("www-authenticate", "")
        signed = await _signed_post(live, "box_a_read", BEAT)
        assert signed.status_code == 403 and signed.json()["detail"] == agent_oauth.BOX_ONLY_CODE
        assert _rows(live) == []

    async def test_a_person_cannot_write_a_heartbeat(self, live):
        for who in (OWNER_A, ADMIN_A):
            r = await _as_member(live, who, "POST", BEAT)
            assert r.status_code == 403, r.text
        assert _rows(live) == []

    async def test_a_revoked_token_writes_nothing(self, live):
        r = await _call(live, "box_revoked", **BEAT)
        assert r.status_code == 401
        signed = await _signed_post(live, "box_revoked", BEAT)
        assert signed.status_code == 401
        assert _rows(live) == []

    async def test_a_body_cannot_name_an_org_or_a_connection(self, live):
        for extra in ({"organization_id": live.org_b}, {"connection_id": live.c["box_b"]},
                      {"member_email": MEMBER_B}):
            r = await _signed_post(live, "box_a", {**BEAT, **extra})
            assert r.status_code == 422, (extra, r.text)
        assert _rows(live) == []


@_DB_GATE
class TestUntrustedValues:

    async def test_the_note_is_stripped_of_control_characters(self, live):
        r = await _signed_post(live, "box_a", {"account_note": "ok\x00\x1b 14:00\x7f"})
        assert r.status_code == 200, r.text
        assert _rows(live)[0].account_note == "ok 14:00"

    async def test_the_edge_refuses_a_nul_in_the_note(self, live):
        assert _error(await _call(live, "box_a", account_note="a\x00b")) == (
            tools.TEXT_BOX_NOTE_NUL)
        assert _rows(live) == []

    @pytest.mark.parametrize("bad", [
        {"mem_free_mb": -1}, {"load_1m": 10001}, {"sessions": 10001},
        {"sign_in": "admin"}, {"account": "paid"}, {"ci_lane_busy": "yes"},
        {"leases": {"BAD KEY": 1}}, {"leases": {"port": -1}},
        {"leases": {f"k{i}": 1 for i in range(9)}}, {"mem_free_mb": True},
    ])
    async def test_a_value_out_of_its_range_is_422(self, live, bad):
        r = await _signed_post(live, "box_a", bad)
        assert r.status_code == 422, (bad, r.text)
        assert _rows(live) == []

    async def test_the_edge_checks_the_ranges_first(self, live):
        assert "load_1m" in _error(await _call(live, "box_a", load_1m=-0.5))
        assert "sign_in" in _error(await _call(live, "box_a", sign_in="admin"))
        assert _rows(live) == []


@_DB_GATE
class TestTheMemberRead:

    async def _beat_all(self, world) -> None:
        for name in ("box_a", "box_other_a", "box_b"):
            _ok(await _call(world, name, sessions=1))

    async def test_a_member_reads_only_own_boxes(self, live):
        await self._beat_all(live)
        assert _names(await _as_member(live, OWNER_A)) == {"metorite-home"}
        assert _names(await _as_member(live, OTHER_A)) == {"other-box"}

    async def test_the_manage_permission_reads_every_box_of_the_org(self, live):
        await self._beat_all(live)
        assert _names(await _as_member(live, ADMIN_A)) == {"metorite-home", "other-box"}

    async def test_org_b_reads_no_box_of_org_a(self, live):
        await self._beat_all(live)
        assert _names(await _as_member(live, MEMBER_B)) == {"b-box"}

        async def raw() -> list[Any]:
            from acb_common.db import tenant_session

            async with tenant_session(live.org_b) as db:
                return list((await db.execute(text(
                    "SELECT CAST(organization_id AS TEXT) AS org FROM agent_box_health"))).all())
        rows = await _in_scope(live, raw)
        assert {r.org for r in rows} == {live.org_b}

    async def test_a_revoked_box_is_not_listed(self, live):
        await self._beat_all(live)
        with live.admin.begin() as c:
            c.execute(text("UPDATE agent_connection SET revoked_at = now()"
                           " WHERE id = CAST(:c AS uuid)"), {"c": live.c["box_other_a"]})
        try:
            assert _names(await _as_member(live, ADMIN_A)) == {"metorite-home"}
        finally:
            with live.admin.begin() as c:
                c.execute(text("UPDATE agent_connection SET revoked_at = NULL"
                               " WHERE id = CAST(:c AS uuid)"), {"c": live.c["box_other_a"]})

    async def test_a_heartbeat_older_than_3_minutes_reads_offline(self, live):
        _ok(await _call(live, "box_a", **BEAT))
        r = await _as_member(live, OWNER_A)
        (box,) = r.json()["boxes"]
        assert box["state"] == "online" and box["sessions"] == 3
        assert box["leases"] == {"database": 2, "port": 1} and box["load_1m"] == 1.25
        for minutes, state in ((2, "online"), (4, "offline")):
            with live.admin.begin() as c:
                c.execute(text("UPDATE agent_box_health SET reported_at = now()"
                               " - make_interval(mins => :m)"), {"m": minutes})
            (box,) = (await _as_member(live, OWNER_A)).json()["boxes"]
            assert box["state"] == state, minutes

    async def test_an_agent_cannot_read_the_list(self, live):
        """The GET is not in ``MCP_ROUTES``, so branch 0 refuses a signed read."""
        from gateway.main import app as gateway_app

        headers = agent_signing.signed_headers(
            KEY, organization_id=live.org_a, connection_id=live.c["box_a"],
            actor_via=_via(live, "box_a"), method="GET", path="/agent-box-health", query="")

        async def go() -> httpx.Response:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                         base_url="http://gateway.test") as client:
                return await client.get("/agent-box-health", headers=headers)
        r = await _in_scope(live, go)
        assert r.status_code == 401


@_DB_GATE
class TestTheFlag:

    async def test_the_flag_off_is_404(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_box_health_orgs", live.org_b)
        assert (await _signed_post(live, "box_a", BEAT)).status_code == 404
        assert (await _as_member(live, OWNER_A)).status_code == 404
        assert _error(await _call(live, "box_a", **BEAT)) == tools.TEXT_BOX_HEALTH_OFF
        assert _rows(live) == []
        # Org B is on the list, so its box still writes.
        _ok(await _call(live, "box_b", sessions=1))
        assert [r.org for r in _rows(live)] == [live.org_b]

    async def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, live):
        eng = create_engine(live.app_dsn)
        try:
            with eng.connect() as c:
                row = c.execute(text(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )).one()
        finally:
            eng.dispose()
        assert (row.rolsuper, row.rolbypassrls) == (False, False)
