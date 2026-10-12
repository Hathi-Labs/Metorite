"""R8 — MCP servers live in the encrypted connection registry (WS-54 IN-4).

Spec: ``project-docs/specs/integrations_console.md`` §3.4, §7.2 (IN-4) and
§7.3 (acceptance A to H). Each server of an org is one registry row,
``provider = 'mcp:<name>'``, ``owning_app = 'integrations'``,
``scope_kind = 'org'``. The header map sits in ``credentials_encrypted`` only,
as Fernet ciphertext. ``gateway.connections.mcp`` is the one reader, and it
opens ``tenant_session(org)``.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (the owner role bypasses RLS, so it would prove nothing):

- **B** a run in org A carries only org A's servers, and a run in org B only
  B's. The org predicate of the store holds on its own too, on a session that
  bypasses RLS;
- **C** org A registers two servers, and both reach the agent, each with its
  own header;
- **D** a sentinel header value is never plain text: not in the blob, not in
  ``provider_meta``, not in any column of the row, not in a GET response, not
  in a read-model row and not in a log line;
- **F** one old ``mcp_servers`` row moves into the registry on a read, once.
  A second read moves nothing, a newer registration wins, the old row stays,
  and a delete removes it, so it cannot come back;
- **G** ``POST /integrations/mcp/{name}/test`` sends the STORED header,
  decrypted, through ``outbound_guard``. Another org's name is 404;
- **H** the read model lists each MCP server once.

**E** (only ``store.py`` names the table) is
``tests/unit/test_crm_connection.py::test_only_the_store_names_the_connections_table``.
**A** is the eight WS-8c suites, which now run against the registry.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_registry_r8.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
import structlog

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="agent_framework not installed")

from acb_common.db import bind_tenant, clear_tenant, release_tenant
from sqlalchemy import text

from tests.unit._maf_mcp_fakes import (
    CopilotStub,
    install_offline,
    logged_text,
    native_agent,
    set_flag,
)
from tests.unit._tenant_ladder import tenant_engine_scope

# ``promoted``, ``app_engine``, ``graph_on_promoted`` and
# ``registry_on_promoted`` are fixtures, used by name.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_mcp_servers_org_scope import (  # noqa: F401
    graph_on_promoted,
    registry_on_promoted,
    seed_registry,
)

# ⚠️ Without this gate the suite ERRORS where no ladder URL is set (the deploy
# run), and the deploy stops.
pytestmark = _DB_GATE

REAL_CLIENT = httpx.AsyncClient

SENTINEL = "Bearer sentinel-in4-registry-7d1c"
SENTINEL_B = "Bearer sentinel-in4-registry-org-b"
LEGACY_SENTINEL = "Bearer sentinel-in4-legacy-row-41af"
LEGACY_ENV = "sentinel-in4-legacy-env-9e02"
ADMIN_A = "admin-a@in4-r8.test"
ADMIN_B = "admin-b@in4-r8.test"
_AGENT = "apis-config"


def _user(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access([
            "feature:integrations", "integrations:manage", "admin:members:read",
        ]),
        organization_id=org,
    )


def _name(tag: str) -> str:
    return f"in4-{tag}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
async def ns(registry_on_promoted, monkeypatch):  # noqa: F811 - a fixture
    """The two-org catalog on the app role, a public DNS answer, no tenant."""
    from gateway import outbound_guard

    monkeypatch.setattr(outbound_guard, "_resolve", lambda _h, _p: ["93.184.216.34"])
    monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
    clear_tenant()
    try:
        yield registry_on_promoted
    finally:
        clear_tenant()


def _assert_non_priv(app_eng: Any) -> None:
    with app_eng.connect() as c:
        row = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
        )).one()
    assert tuple(row) == (False, False), "the app role must not bypass RLS"


async def _call(user: Any, method: str, path: str, body: dict | None = None) -> Any:
    """One request through the real routers, as *user*. The tenant comes from
    the user, never from the request."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes import integrations, integrations_connections

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(integrations.router)
    app.include_router(integrations_connections.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with REAL_CLIENT(
            transport=httpx.ASGITransport(app=app), base_url="http://integrations.test",
        ) as client:
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


async def _register(user: Any, name: str, *, url: str = "https://mcp.example.com/mcp",
                    headers: dict[str, str] | None = None, enabled: bool = True) -> None:
    res = await _call(user, "POST", "/integrations/mcp", {
        "name": name, "transport": "http-sse", "url": url,
        "headers": headers or {}, "enabled": enabled,
    })
    assert res.status_code == 201, res.text


async def _inject(org: str, make: Any = native_agent) -> Any:
    from orchestrator._tool_injection import _inject_mcp_servers

    agent = make()
    token = bind_tenant(org)
    try:
        await _inject_mcp_servers(agent, _AGENT, organization_id=org, opened=[])
    finally:
        release_tenant(token)
    return agent


def _registry(p: Any, org: str) -> dict[str, Any]:
    """The MCP rows of *org*, by name, read as the admin (no RLS)."""
    with p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT provider, owning_app, scope_kind, member_email, status, "
            "provider_meta, credentials_encrypted, connected_by, updated_at, "
            "row_to_json(t)::text AS whole FROM integration_connections t "
            "WHERE organization_id = CAST(:o AS uuid) AND provider LIKE 'mcp:%'"),
            {"o": org}).mappings().all()
    return {r["provider"].removeprefix("mcp:"): dict(r) for r in rows}


def _seed_legacy(p: Any, org: str, name: str, *, transport: str = "http-sse",
                 url: str | None = "https://legacy.example.com/mcp") -> None:
    """One old ``mcp_servers`` row, as the code before IN-4 wrote it."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO mcp_servers (name, label, description, transport, "
            "command, url, env_vars, headers, agent_scope, enabled, "
            "organization_id) VALUES (:n, :n, 'old row', :t, :cmd, :u, "
            "CAST(:e AS jsonb), CAST(:h AS jsonb), '[\"*\"]'::jsonb, true, "
            "CAST(:o AS uuid))"),
            {"n": name, "t": transport, "u": url, "o": org,
             "cmd": "npx -y srv" if transport == "stdio" else None,
             "e": json.dumps({"API_KEY": LEGACY_ENV}),
             "h": json.dumps({"Authorization": LEGACY_SENTINEL})})


def _legacy_count(p: Any, org: str, name: str) -> int:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT count(*) FROM mcp_servers WHERE organization_id = "
            "CAST(:o AS uuid) AND name = :n"), {"o": org, "n": name}).scalar_one()


def _moved(logs: list[dict[str, Any]]) -> list[str]:
    return [e["name"] for e in logs if e["event"] == "mcp.legacy_moved"]


# ── B: a run carries only the servers of its own org ────────────────────────


async def test_b_a_run_in_org_a_carries_only_org_a_servers(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    set_flag(monkeypatch, "*")
    offline = install_offline(monkeypatch)
    name_a, name_b = _name("a"), _name("b")
    await _register(_user(ADMIN_A, ns.org_a), name_a, headers={"Authorization": SENTINEL})
    await _register(_user(ADMIN_B, ns.org_b), name_b, headers={"Authorization": SENTINEL_B})

    agent_a = await _inject(ns.org_a)
    agent_b = await _inject(ns.org_b)

    assert [t.name for t in agent_a.mcp_tools] == [name_a]
    assert agent_a.mcp_tools[0]._static_headers == {"Authorization": SENTINEL}
    assert [t.name for t in agent_b.mcp_tools] == [name_b]
    assert agent_b.mcp_tools[0]._static_headers == {"Authorization": SENTINEL_B}
    assert len(offline["clients"]) == 2
    # Not vacuous: the admin sees both rows.
    assert name_a in _registry(ns, ns.org_a) and name_b in _registry(ns, ns.org_b)


async def test_b_a_copilot_agent_still_gets_no_registry_server(ns, monkeypatch) -> None:
    set_flag(monkeypatch, "*")
    name_a = _name("cp")
    await _register(_user(ADMIN_A, ns.org_a), name_a, headers={"Authorization": SENTINEL})
    with structlog.testing.capture_logs() as logs:
        agent = await _inject(ns.org_a, CopilotStub)
    assert agent._default_options == {}
    assert [e["name"] for e in logs if e["event"] == "mcp.copilot_registry_skipped"] == [name_a]
    assert SENTINEL not in logged_text(logs)


async def test_b_the_store_predicate_holds_on_a_session_that_bypasses_rls(ns) -> None:
    """Each store query names the org, so a session with RLS off still reads
    one org (``store.py``'s rule). The admin is a superuser: RLS does not
    filter it, so only the predicate can."""
    from acb_common.db import tenant_session
    from gateway.connections import mcp, store

    name_a, name_b = _name("pa"), _name("pb")
    await _register(_user(ADMIN_A, ns.org_a), name_a, headers={"Authorization": SENTINEL})
    await _register(_user(ADMIN_B, ns.org_b), name_b, headers={"Authorization": SENTINEL_B})
    admin_dsn = ns.admin_engine.url.render_as_string(hide_password=False)
    async with tenant_engine_scope(admin_dsn), tenant_session(ns.org_a) as db:
        seen = (await db.execute(text(
            "SELECT count(*) FROM integration_connections WHERE provider LIKE 'mcp:%'",
        ))).scalar_one()
        conns = await store.list_by_prefix(db, ns.org_a, mcp.PREFIX, mcp.OWNING_APP)
        blobs = await store.load_headers_by_prefix(db, ns.org_a, mcp.PREFIX, mcp.OWNING_APP)
    assert seen >= 2, "the admin session must see both orgs, or this proves nothing"
    assert [c.provider for c in conns] == [f"mcp:{name_a}"]
    assert blobs == {f"mcp:{name_a}": {"Authorization": SENTINEL}}


# ── C: two servers of one org both reach the agent ──────────────────────────


async def test_c_two_servers_of_one_org_both_reach_the_agent(ns, monkeypatch) -> None:
    set_flag(monkeypatch, "*")
    install_offline(monkeypatch)
    user = _user(ADMIN_A, ns.org_a)
    one, two, off = _name("c1"), _name("c2"), _name("off")
    await _register(user, one, url="https://one.example.com/mcp",
                    headers={"Authorization": "Bearer one"})
    await _register(user, two, url="https://two.example.com/mcp",
                    headers={"X-Api-Key": "two"})
    await _register(user, off, enabled=False, headers={"Authorization": "Bearer off"})

    agent = await _inject(ns.org_a)

    tools = {t.name: t for t in agent.mcp_tools}
    assert set(tools) == {one, two}, "a disabled server must not reach the agent"
    assert tools[one]._static_headers == {"Authorization": "Bearer one"}
    assert tools[two]._static_headers == {"X-Api-Key": "two"}
    assert tools[one].url == "https://one.example.com/mcp"
    rows = _registry(ns, ns.org_a)
    assert {rows[n]["status"] for n in (one, two)} == {"connected"}
    assert rows[off]["status"] == "disconnected" and rows[off]["credentials_encrypted"]
    for n in (one, two, off):
        assert (rows[n]["owning_app"], rows[n]["scope_kind"], rows[n]["member_email"]) == (
            "integrations", "org", None)


# ── D: a sentinel header value is never plain text ──────────────────────────


async def test_d_a_sentinel_header_is_never_plain_text(ns, monkeypatch, caplog) -> None:
    from gateway import outbound_guard

    set_flag(monkeypatch, "*")
    install_offline(monkeypatch, secret=SENTINEL)

    async def _fetch(method: str, url: str, **_: Any) -> Any:
        return outbound_guard.Answer(status_code=200, body=b"{}")

    monkeypatch.setattr(outbound_guard, "request", _fetch)
    user = _user(ADMIN_A, ns.org_a)
    name = _name("d")
    bodies: list[str] = []
    with structlog.testing.capture_logs() as logs:
        await _register(user, name, headers={"Authorization": SENTINEL})
        listed = await _call(user, "GET", "/integrations/mcp")
        bodies.append(listed.text)
        bodies.append((await _call(user, "GET", "/integrations/connections")).text)
        bodies.append((await _call(user, "POST", f"/integrations/mcp/{name}/test")).text)
        await _inject(ns.org_a)

    row = _registry(ns, ns.org_a)[name]
    assert row["credentials_encrypted"] and SENTINEL not in row["credentials_encrypted"]
    meta = json.dumps(row["provider_meta"])
    assert SENTINEL not in meta and "headers" not in row["provider_meta"]
    assert SENTINEL not in row["whole"], "a column of the row holds the header in plain text"
    assert _legacy_count(ns, ns.org_a, name) == 0, "a register wrote mcp_servers"
    mine = next(r for r in listed.json() if r["name"] == name)
    assert mine["header_names"] == ["Authorization"]
    everything = "\n".join(bodies) + "\n" + logged_text(logs, caplog)
    assert SENTINEL not in everything and "sentinel-in4" not in everything


# ── F: an old mcp_servers row moves once ────────────────────────────────────


async def test_f_one_legacy_row_moves_once_and_stays(ns, monkeypatch) -> None:
    from gateway.connections import mcp

    set_flag(monkeypatch, "*")
    install_offline(monkeypatch)
    user = _user(ADMIN_A, ns.org_a)
    name = _name("old")
    _seed_legacy(ns, ns.org_a, name)
    assert _registry(ns, ns.org_a) == {}

    with structlog.testing.capture_logs() as first:
        listed = (await _call(user, "GET", "/integrations/mcp")).json()
    assert [r["name"] for r in listed] == [name]
    assert listed[0]["header_names"] == ["Authorization"] and listed[0]["env_var_names"] == []
    assert _moved(first) == [name]
    row = _registry(ns, ns.org_a)[name]
    assert row["connected_by"] == mcp.MIGRATED_BY and row["status"] == "connected"
    assert LEGACY_SENTINEL not in row["whole"] and LEGACY_ENV not in row["whole"]
    assert row["provider_meta"]["url"] == "https://legacy.example.com/mcp"
    assert _legacy_count(ns, ns.org_a, name) == 1, "the old row must stay for one release"

    # A second read, through the run and the console, moves nothing.
    with structlog.testing.capture_logs() as second:
        agent = await _inject(ns.org_a)
        await _call(user, "GET", "/integrations/connections")
    assert _moved(second) == []
    assert len(_registry(ns, ns.org_a)) == 1
    assert _registry(ns, ns.org_a)[name]["updated_at"] == row["updated_at"]
    assert agent.mcp_tools[0]._static_headers == {"Authorization": LEGACY_SENTINEL}

    # A newer registration wins over the old row, on every later read.
    await _register(user, name, url="https://new.example.com/mcp",
                    headers={"Authorization": "Bearer new"})
    await _call(user, "GET", "/integrations/mcp")
    assert _registry(ns, ns.org_a)[name]["provider_meta"]["url"] == "https://new.example.com/mcp"

    # Org B's read moves nothing of org A's.
    await _call(_user(ADMIN_B, ns.org_b), "GET", "/integrations/mcp")
    assert _registry(ns, ns.org_b) == {}

    # A delete removes the old row too, so the next read cannot bring it back.
    assert (await _call(user, "DELETE", f"/integrations/mcp/{name}")).status_code == 200
    assert _legacy_count(ns, ns.org_a, name) == 0
    assert (await _call(user, "GET", "/integrations/mcp")).json() == []
    assert _registry(ns, ns.org_a) == {}


async def test_f_a_legacy_stdio_row_does_not_move(ns) -> None:
    name = _name("io")
    _seed_legacy(ns, ns.org_a, name, transport="stdio", url=None)
    listed = (await _call(_user(ADMIN_A, ns.org_a), "GET", "/integrations/mcp")).json()
    assert listed == [] and _registry(ns, ns.org_a) == {}


# ── G: the stored test sends the decrypted header through the guard ─────────


async def test_g_the_stored_test_sends_the_decrypted_header_through_the_guard(
    ns, monkeypatch,
) -> None:
    from gateway import outbound_guard

    sent: list[tuple[str, str, dict[str, str]]] = []

    async def _fetch(method: str, url: str, *, headers: dict[str, str] | None = None,
                     **_: Any) -> Any:
        sent.append((method, url, dict(headers or {})))
        return outbound_guard.Answer(status_code=200, body=b"{}")

    monkeypatch.setattr(outbound_guard, "request", _fetch)
    name_a, name_b = _name("ga"), _name("gb")
    # Written by the registry writer itself, so only a decrypt of the blob
    # can give the route the header.
    await seed_registry(ns.org_a, name_a, url="https://a.example.com/mcp",
                        headers={"Authorization": SENTINEL})
    await seed_registry(ns.org_b, name_b, headers={"Authorization": SENTINEL_B})
    assert SENTINEL not in _registry(ns, ns.org_a)[name_a]["whole"]

    res = await _call(_user(ADMIN_A, ns.org_a), "POST", f"/integrations/mcp/{name_a}/test")
    assert res.status_code == 200 and res.json() == {"ok": True, "tools": None, "error": None}
    assert len(sent) == 1
    method, url, headers = sent[0]
    assert (method, url) == ("GET", "https://a.example.com/mcp")
    assert headers["Authorization"] == SENTINEL
    assert SENTINEL not in res.text

    other = await _call(_user(ADMIN_A, ns.org_a), "POST", f"/integrations/mcp/{name_b}/test")
    assert other.status_code == 404 and len(sent) == 1, "a test reached another org's server"


# ── H: the read model lists each MCP server once ────────────────────────────


async def test_h_the_read_model_lists_each_mcp_server_once(ns) -> None:
    user = _user(ADMIN_A, ns.org_a)
    one, two = _name("h1"), _name("h2")
    await _register(user, one, headers={"Authorization": SENTINEL})
    await _register(user, two)

    res = await _call(user, "GET", "/integrations/connections")
    assert res.status_code == 200, res.text
    rows = res.json()["connections"]
    mcp_rows = [r for r in rows if r["kind"] == "mcp"]
    assert sorted(r["id"] for r in mcp_rows) == sorted([f"mcp:{one}", f"mcp:{two}"])
    for name in (one, two):
        hits = [r for r in rows if name in json.dumps(r)]
        assert len(hits) == 1, f"{name} is listed {len(hits)} times: {hits}"
    assert not any(r["provider"].startswith("mcp:") for r in rows), (
        "the registry source listed an MCP row a second time")

    # The disconnect of the console removes the row of the registry.
    out = await _call(user, "POST", f"/integrations/connections/mcp:{one}/disconnect")
    assert out.status_code == 200 and out.json() == {"removed": True}
    assert set(_registry(ns, ns.org_a)) == {two}
