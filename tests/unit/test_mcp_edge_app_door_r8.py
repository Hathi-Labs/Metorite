"""MCP-P1a, R8 — the app door end to end, on a real database.

Spec: ``project-docs/specs/external_agent_access.md`` §19.4, §19.5, §19.7,
§19.8 and the "Done when" of the MCP-P1a row of §19.13.

The edge calls the REAL gateway app through ``httpx.ASGITransport``
(EA3-20). Branch 0, the real Knowledge router and its one visibility rule
(``core.visible_pages_clause``) run on a private, phase-4-promoted catalog,
as a NON-privileged role (no superuser, no BYPASSRLS). Branch 0 keeps its
counters, and the edge its idempotency slot, in a fake under the REAL
``TenantRedis``, so CI needs no Redis.

What it proves:

1. An agent lists its apps, describes Knowledge, reads a page and files a
   proposed edit. The edit waits in the Knowledge Review list, and the page
   does not change.
2. A connection with no Knowledge scope gets the 403 ``insufficient_scope``
   and reveals no data. Branch 0 refuses its signed read too.
3. The member's visibility decides each row: member A and member B see
   different pages through the same op.
4. A class B door op that is not a proposal gets the 403
   ``approval_required`` at branch 0, the route never runs, and the edge
   sends no request for it (AD-18). A test variant of the Knowledge manifest
   gives the real page PATCH an op for this.
5. Each door call writes one audit row (AD-14).
6. D-EA-4 (§19.8): a connection with ``include_personal`` false sees no
   ``me`` space and no page of one. A connection with it true sees them, and
   so does the member's own request with no agent.

Mutation (R7): restore the ``me`` arm for an agent in
``routes/knowledge/core.py`` (make ``_hides_personal`` answer False) ->
``test_a_connection_without_personal_sees_no_me_page``.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_app_door_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth import agent_signing, agent_tokens
from acb_auth.access import invalidate as invalidate_access
from acb_common import app_manifests as am
from acb_common import get_settings, tenant_redis
from acb_common.tenant_redis import TenantRedis
from agent_edge import app as edge_app_module
from agent_edge import gateway_client, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import _LADDER_DIR, apply_ladder, tenant_engine_scope
from tests.unit.test_mcp_edge_task_writes import _FakeRedis
from tests.unit.test_mcp_edge_writes import _DB_GATE, _URL, _drop_database, _exec_sql_file

# Every test here needs the real database. Without the gate the suite errors
# where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

ISSUER = "https://mcp.metorite.com"
RESOURCE = "https://mcp.metorite.com/mcp"
KEY = "mcp-app-door-signing-key-" + "a" * 24
INTERNAL = "mcp-app-door-internal-token"
VIA = "mcp:client.example:meta"
ORIGIN = "https://client.example"
DOMAIN = "appdoor.test"
MEMBER_A = f"member-a@{DOMAIN}"
MEMBER_B = f"member-b@{DOMAIN}"
OWNER = f"owner@{DOMAIN}"

_APP_ROLE = "acb_app_mcpappdoor"
_APP_PW = "mcpappdoor_scratch_pw"
_GENERATED = os.path.join(_LADDER_DIR, "generated")


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
            o=self.org, s="door-" + email.split("@")[0])
        for permission in permissions:
            self.one("INSERT INTO org_role_permission (role_id, permission)"
                     " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                     r=str(role), p=permission)
        self.one("INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
                 " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))

    def space(self, slug: str, name: str, grants: dict[str, str]) -> str:
        sid = str(self.one(
            "INSERT INTO kb_spaces (organization_id, slug, name, kind, owner_email)"
            " VALUES (CAST(:o AS uuid), :s, :n, 'team', :owner) RETURNING id",
            o=self.org, s=slug, n=name, owner=OWNER))
        for subject, level in grants.items():
            self.one("INSERT INTO kb_space_grants (organization_id, space_id, subject, level,"
                     " created_by) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), :sub, :lvl,"
                     " :by) RETURNING space_id",
                     o=self.org, s=sid, sub=subject, lvl=level, by=OWNER)
        return sid

    def page(self, space: str, title: str, body: str) -> str:
        pid = str(self.one(
            "INSERT INTO kb_pages (organization_id, space_id, title, body_md, state,"
            " author_email, published_at) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), :t,"
            " :b, 'published', :a, now()) RETURNING id",
            o=self.org, s=space, t=title, b=body, a=OWNER))
        self.one("INSERT INTO kb_page_versions (organization_id, page_id, version, body_md,"
                 " title, author_email, reason) VALUES (CAST(:o AS uuid), CAST(:p AS uuid),"
                 " 1, :b, :t, :a, 'create') RETURNING id",
                 o=self.org, p=pid, b=body, t=title, a=OWNER)
        return pid

    def me_space(self, owner: str) -> str:
        """The ``me`` space of *owner*, as ``core.INSERT_ME_SPACE_SQL`` makes it."""
        return str(self.one(
            "INSERT INTO kb_spaces (organization_id, slug, name, purpose, kind, owner_email)"
            " VALUES (CAST(:o AS uuid), 'me', 'My notes', 'Your private drafts.', 'me', :w)"
            " RETURNING id", o=self.org, w=owner))

    def draft(self, space: str, title: str, body: str, author: str) -> str:
        """A draft of *author* in *space*, with its first version."""
        pid = str(self.one(
            "INSERT INTO kb_pages (organization_id, space_id, title, body_md, state,"
            " author_email) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), :t, :b, 'draft', :a)"
            " RETURNING id", o=self.org, s=space, t=title, b=body, a=author))
        self.one("INSERT INTO kb_page_versions (organization_id, page_id, version, body_md,"
                 " title, author_email, reason) VALUES (CAST(:o AS uuid), CAST(:p AS uuid),"
                 " 1, :b, :t, :a, 'create') RETURNING id",
                 o=self.org, p=pid, b=body, t=title, a=author)
        return pid

    def connection(self, member: str, scopes: tuple[str, ...],
                   include_personal: bool = False) -> tuple[str, str]:
        """A live connection and one access token for it: ``(id, raw token)``."""
        cid = str(self.one(
            "INSERT INTO agent_connection (organization_id, member_email, client_id,"
            " client_name, client_origin, scopes, pinned_projects, include_personal,"
            " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
            " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), :personal,"
            " now() + interval '1 day') RETURNING id",
            o=self.org, m=member, origin=ORIGIN, scopes=list(scopes), pins=[],
            personal=include_personal))
        raw = agent_tokens.new_token()
        self.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
                 " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
                 " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour',"
                 " 'https://client.example/meta.json', :r, :s) RETURNING id",
                 o=self.org, c=cid, h=agent_tokens.hash_token(raw), r=RESOURCE,
                 s=" ".join(scopes))
        return cid, raw


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog, one tenant, two members, two spaces."""
    if not _URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_mcpappdoor"
    _drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(_GENERATED, phase),
                                       encoding="utf-8") as fh:
            _exec_sql_file(conn, fh.read())
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

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'mcpappdoor', 'mcpappdoor')"), {"id": org})
    a = _Seeder(eng, org)
    for email in (MEMBER_A, MEMBER_B):
        a.member(email, ["feature:knowledge"])
    team = a.space("team", "Team", {MEMBER_A: "write"})
    other = a.space("other", "Other", {MEMBER_B: "read"})
    me = a.me_space(MEMBER_A)
    w = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, other=other, me=me,
                        page_a=a.page(team, "Team plan", "# Plan"),
                        page_b=a.page(other, "Other plan", "# Secret"),
                        page_me=a.draft(me, "Private note", "# Mine", MEMBER_A), c={}, tok={})
    for name, member, scopes, personal in (
            ("a", MEMBER_A, ("knowledge:read", "knowledge:write"), False),
            ("a_personal", MEMBER_A, ("knowledge:read",), True),
            ("a_projects", MEMBER_A, ("projects:read",), False),
            ("b", MEMBER_B, ("knowledge:read",), False)):
        w.c[name], w.tok[name] = a.connection(member, scopes, include_personal=personal)
    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield w

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def live(monkeypatch, world):
    """The real gateway app at ``agent_edge_gateway_url``, the door serving
    Knowledge, Knowledge and its proposals on, and the audit rows caught."""
    import acb_audit
    from gateway.main import app as gateway_app

    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_access_orgs", "*"), ("agent_edge_signing_key", KEY),
            ("agent_edge_signing_key_previous", ""), ("agent_issuer", ISSUER),
            ("agent_resource", RESOURCE), ("agent_edge_gateway_url", "http://gateway.test"),
            ("agent_door_apps", "knowledge"), ("knowledge_enabled", True),
            ("knowledge_orgs", "*"), ("knowledge_proposals", True)):
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    redis = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(redis))
    audit: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", audit.append)
    invalidate_access()
    world.redis, world.audit = redis, audit
    with gateway_client.use_transport(httpx.ASGITransport(app=gateway_app)):
        try:
            yield world
        finally:
            invalidate_access()


async def _in_scope(world, work) -> Any:
    """Run *work* in its own task, on the suite's non-privileged DSN."""
    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _mcp(world, name: str, tool: str, arguments: dict[str, Any]) -> httpx.Response:
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                base_url=ISSUER) as client:
            return await client.post("/mcp", content=json.dumps(message), headers={
                "authorization": f"Bearer {world.tok[name]}",
                "content-type": "application/json", "mcp-protocol-version": "2025-11-25"})
    return await _in_scope(world, go)


async def _result(world, name: str, tool: str, **arguments: Any) -> dict[str, Any]:
    r = await _mcp(world, name, tool, arguments)
    assert r.status_code == 200, r.text
    return r.json()["result"]


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    assert result["isError"] is False, result
    return result["structuredContent"]


def _text(result: dict[str, Any]) -> str:
    assert result["isError"] is True, result
    return result["content"][0]["text"]


async def _signed(world, name: str, method: str, path: str, body: bytes = b"") -> httpx.Response:
    """One request that the test signs itself, to the gateway app."""
    from gateway.main import app as gateway_app

    headers = agent_signing.signed_headers(
        KEY, organization_id=world.org, connection_id=world.c[name], actor_via=VIA,
        method=method, path=path, query="", body=body)
    if method != "GET":
        headers["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=headers, content=body)
    return await _in_scope(world, go)


async def _as_member(world, who: str, path: str) -> httpx.Response:
    """One GET as the member *who* in the app: the internal bearer and the
    member's email (branch 1a). No ``UserContext.agent`` is set."""
    from gateway.main import app as gateway_app

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.get(path, headers={"Authorization": f"Bearer {INTERNAL}",
                                                   "X-User-Email": who})
    return await _in_scope(world, go)


def _titles(out: dict[str, Any]) -> set[str]:
    return {row["title"]["untrusted_text"] for row in out["result"]["pages"]}


def _decisions(world) -> list[tuple[str, str]]:
    return [(e.payload["op"], e.payload["decision"]) for e in world.audit]


class TestTheDoor:

    async def test_an_agent_lists_its_apps(self, live):
        out = _ok(await _result(live, "a", "list_apps"))
        rows = {row["id"]: row for row in out["apps"]}
        # The member holds feature:knowledge and not feature:projects.
        assert set(rows) == {"knowledge"}
        assert (rows["knowledge"]["read"], rows["knowledge"]["write"]) == (True, True)
        out = _ok(await _result(live, "b", "list_apps"))
        assert out["apps"][0]["write"] is False

    async def test_an_agent_describes_knowledge(self, live):
        out = _ok(await _result(live, "a", "describe_app", app="knowledge"))
        ops = {row["op"]: row for row in out["ops"]}
        assert set(ops) == set(am.door_ops(am.KNOWLEDGE))
        assert ops["propose"]["available"] is True
        assert ops["propose"]["args"]["properties"]["kind"]["enum"] == ["page", "edit"]
        assert "page_id" in ops["get_page"]["args"]["properties"]

    async def test_an_agent_reads_a_page_as_the_member(self, live):
        out = _ok(await _result(live, "a", "app_read", app="knowledge", op="get_page",
                                args={"page_id": live.page_a}))
        page = out["result"]
        assert page["id"] == live.page_a and page["state"] == "published"
        assert page["title"] == {"untrusted_text": "Team plan"}
        assert page["body_md"] == {"untrusted_text": "# Plan"}
        assert page["versions"][0]["version"] == 1

    async def test_the_members_visibility_decides_each_row(self, live):
        a = _ok(await _result(live, "a", "app_read", app="knowledge", op="list_pages"))
        b = _ok(await _result(live, "b", "app_read", app="knowledge", op="list_pages"))
        assert "Team plan" in _titles(a) and "Other plan" not in _titles(a)
        assert "Other plan" in _titles(b) and "Team plan" not in _titles(b)
        hidden = await _result(live, "a", "app_read", app="knowledge", op="get_page",
                               args={"page_id": live.page_b})
        assert _text(hidden) == "Knowledge has nothing at that id."

    async def test_no_knowledge_scope_is_403_and_reveals_nothing(self, live):
        r = await _mcp(live, "a_projects", "app_read",
                       {"app": "knowledge", "op": "get_page", "args": {"page_id": live.page_a}})
        assert r.status_code == 403
        assert 'scope="knowledge:read"' in r.headers["www-authenticate"]
        assert "Team plan" not in r.text and "Plan" not in r.text
        # Branch 0 refuses the same read when a key holder signs it itself.
        signed = await _signed(live, "a_projects", "GET", f"/knowledge/pages/{live.page_a}")
        assert signed.status_code == 401 and "Team plan" not in signed.text
        assert ("get_page", "door_scope") in _decisions(live)

    async def test_a_proposed_edit_waits_in_the_review_list(self, live):
        out = _ok(await _result(
            live, "a", "app_write", app="knowledge", op="propose",
            idempotency_key=f"edit-{uuid.uuid4().hex[:12]}",
            args={"kind": "edit", "page_id": live.page_a, "base_version": 1,
                  "body_md": "# Plan v2", "summary": "Add the dates"}))
        assert out["state"] == "done" and out["finish_in"] == "Knowledge, Review"
        proposal = out["result"]["id"]
        row = live.seeder.one(
            "SELECT status || '|' || via || '|' || proposer_email FROM kb_page_proposals"
            " WHERE id = CAST(:i AS uuid)", i=proposal)
        assert row == f"pending|mcp|{MEMBER_A}"
        # The page does not change until a person accepts the edit.
        assert live.seeder.one("SELECT body_md FROM kb_pages WHERE id = CAST(:p AS uuid)",
                               p=live.page_a) == "# Plan"
        listed = _ok(await _result(live, "a", "app_read", app="knowledge",
                                   op="list_proposals"))
        assert proposal in {p["id"] for p in listed["result"]["proposals"]}

    async def test_a_proposed_page_is_a_draft_of_the_member(self, live):
        out = _ok(await _result(
            live, "a", "app_write", app="knowledge", op="propose",
            idempotency_key=f"page-{uuid.uuid4().hex[:12]}",
            args={"kind": "page", "title": "New idea", "body_md": "Text"}))
        page = out["result"]["id"]
        assert live.seeder.one(
            "SELECT state || '|' || (origin->>'kind') || '|' || (origin->>'via')"
            " FROM kb_pages WHERE id = CAST(:p AS uuid)", p=page) == "draft|agent_proposal|mcp"
        hidden = await _result(live, "b", "app_read", app="knowledge", op="get_page",
                               args={"page_id": page})
        assert _text(hidden) == "Knowledge has nothing at that id."

    async def test_each_door_call_writes_one_audit_row(self, live):
        await _result(live, "a", "app_read", app="knowledge", op="list_spaces")
        await _result(live, "a", "app_read", app="knowledge", op="get_page",
                      args={"page_id": live.page_a})
        assert _decisions(live) == [("list_spaces", "allowed"), ("get_page", "allowed")]
        for event in live.audit:
            assert event.organization_id == live.org
            assert event.payload["member"] == MEMBER_A
            assert event.payload["connection_id"] == live.c["a"]
            assert event.payload["via"] == VIA and event.payload["request_id"] is None
            assert set(event.payload) == {"organization_id", "connection_id", "member",
                                          "via", "app", "op", "class", "request_id",
                                          "decision"}

    async def test_a_connection_without_personal_sees_no_me_page(self, live):
        """D-EA-4, §19.8. ``include_personal`` false: the member's ``me`` space
        and its draft do not show through ``get_page``, ``list_spaces`` or
        ``list_recent``. The hidden page answers the text of any hidden page,
        so its existence does not leak."""
        hidden = await _result(live, "a", "app_read", app="knowledge", op="get_page",
                               args={"page_id": live.page_me})
        other = await _result(live, "a", "app_read", app="knowledge", op="get_page",
                              args={"page_id": live.page_b})
        assert _text(hidden) == _text(other) == "Knowledge has nothing at that id."
        spaces = _ok(await _result(live, "a", "app_read", app="knowledge", op="list_spaces"))
        rows = spaces["result"]["spaces"]
        assert live.team in {s["id"] for s in rows}
        assert live.me not in {s["id"] for s in rows}
        assert "me" not in {s["kind"] for s in rows}
        recent = _ok(await _result(live, "a", "app_read", app="knowledge", op="list_recent"))
        ids = {p["id"] for p in recent["result"]["pages"]}
        assert live.page_a in ids and live.page_me not in ids

    async def test_a_connection_with_personal_sees_the_me_page(self, live):
        """The control of the test above: ``include_personal`` true shows the
        same page through the same three ops."""
        page = _ok(await _result(live, "a_personal", "app_read", app="knowledge",
                                 op="get_page", args={"page_id": live.page_me}))
        assert page["result"]["id"] == live.page_me
        assert page["result"]["title"] == {"untrusted_text": "Private note"}
        spaces = _ok(await _result(live, "a_personal", "app_read", app="knowledge",
                                   op="list_spaces"))
        assert live.me in {s["id"] for s in spaces["result"]["spaces"]}
        recent = _ok(await _result(live, "a_personal", "app_read", app="knowledge",
                                   op="list_recent"))
        assert live.page_me in {p["id"] for p in recent["result"]["pages"]}

    async def test_the_members_own_request_still_sees_the_me_page(self, live):
        """The narrowing reads ``user.agent`` only. The member's own request
        in the app (branch 1a, no agent) still opens the ``me`` page."""
        r = await _as_member(live, MEMBER_A, f"/knowledge/pages/{live.page_me}")
        assert r.status_code == 200, r.text
        assert r.json()["title"] == "Private note"
        r = await _as_member(live, MEMBER_A, "/knowledge/spaces")
        assert r.status_code == 200, r.text
        assert live.me in {s["id"] for s in r.json()["spaces"]}
        r = await _as_member(live, MEMBER_A, "/knowledge/recent")
        assert r.status_code == 200, r.text
        assert live.page_me in {p["id"] for p in r.json()["pages"]}

    async def test_the_suite_runs_as_a_role_that_cannot_bypass_rls(self, live):
        engine = create_engine(make_url(live.app_dsn).set(drivername="postgresql+psycopg"))
        try:
            with engine.connect() as c:
                flags = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles"
                                       " WHERE rolname = current_user")).first()
        finally:
            engine.dispose()
        assert tuple(flags) == (False, False)


#: A test variant of the Knowledge manifest: the real page PATCH, a class B
#: row, gets a door op. AD-18 must refuse it, because it is not a proposal.
def _variant() -> am.App:
    rows = tuple(
        dataclasses.replace(r, off_door="", op="update_page", title="Change a page.")
        if (r.method, r.path) == ("PATCH", "/knowledge/pages/{page_id}") else r
        for r in am.KNOWLEDGE.routes)
    return dataclasses.replace(am.KNOWLEDGE, routes=rows)


class TestAd18OnTheRealRoute:

    async def test_branch_0_refuses_the_write_and_the_page_stays(self, live, monkeypatch):
        monkeypatch.setattr(am, "APPS", (_variant(),))
        body = json.dumps({"title": "Changed by an agent"}).encode()
        r = await _signed(live, "a", "PATCH", f"/knowledge/pages/{live.page_a}", body)
        assert r.status_code == 403, r.text
        assert r.json() == {"detail": "approval_required"}
        assert live.seeder.one("SELECT title FROM kb_pages WHERE id = CAST(:p AS uuid)",
                               p=live.page_a) == "Team plan"
        assert _decisions(live) == [("update_page", "approval_required")]

    async def test_the_edge_sends_no_request_for_it(self, live, monkeypatch):
        monkeypatch.setattr(am, "APPS", (_variant(),))
        result = await _result(live, "a", "app_write", app="knowledge", op="update_page",
                               idempotency_key=f"upd-{uuid.uuid4().hex[:12]}",
                               args={"page_id": live.page_a, "title": "Changed"})
        assert _text(result) == tools.TEXT_NEEDS_APPROVAL
        assert live.audit == [], "no request reached branch 0"
        described = _ok(await _result(live, "a", "describe_app", app="knowledge"))
        update = next(o for o in described["ops"] if o["op"] == "update_page")
        assert update["available"] is False and update["asks"] == "each_call"
