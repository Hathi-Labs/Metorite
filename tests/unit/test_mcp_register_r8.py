"""WS-8c A9 (IN-D6) — MCP register and plugin install write ONE org's row (R8).

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c, acceptance A9.
The defect is IN-D6 of ``integrations_console.md``: both INSERTs put ``%s``
inside ``text()``, wrote no ``organization_id``, and used ``ON CONFLICT
(name)`` against a key that migration 158 changed. So both answered 500 on a
migrated database, and no test saw it.

These fences run the REAL routes against a REAL Postgres with two orgs, as
the non-privileged role (``graph_on_promoted``). ⚠️ ``schema.generated.sql``
still keys ``mcp_servers`` by name only, so a fake built from it would agree
with the old SQL. That is why this is R8.

* Orgs A and B register one name, and the table holds two rows.
* A registers the name again, and A's row changes. B's row does not.
* A request with no tenant gets 403, and no row is written.
* Plugin install: the same three facts, on ``plugins_org_name_key``. The
  ``plugins`` table has FORCE RLS, so the install binds the tenant.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_register_r8.py -v -rs
"""
from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from fastapi import HTTPException
from gateway import outbound_guard
from gateway.routes import integrations
from sqlalchemy import text

# ``promoted`` and ``graph_on_promoted`` are fixtures, used by name.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_mcp_servers_org_scope import graph_on_promoted  # noqa: F401

pytestmark = _DB_GATE

USER = UserContext(email="admin@example.test", role=UserRole.EXECUTIVE)


async def _as(org: str | None, call):
    token = bind_tenant(org) if org else clear_tenant()
    try:
        return await call()
    finally:
        release_tenant(token)


def _mcp_rows(admin_engine, name: str) -> dict[str, tuple]:
    with admin_engine.begin() as c:
        rows = c.execute(
            text("SELECT organization_id::text, url, headers, label FROM mcp_servers "
                 "WHERE name = :n"),
            {"n": name},
        ).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


@pytest.fixture
def name(graph_on_promoted, monkeypatch):  # noqa: F811
    p = graph_on_promoted
    # Review round 2: POST /mcp checks the URL with the guard. The test hosts
    # resolve to one public address, so the check passes with no network.
    monkeypatch.setattr(outbound_guard, "_resolve", lambda _h, _p: ["93.184.216.34"])
    monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
    n = f"reg-{uuid.uuid4().hex[:8]}"
    clear_tenant()
    try:
        yield p, n
    finally:
        clear_tenant()
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM mcp_servers WHERE name = :n"), {"n": n})


def _req(n: str, url: str, token: str) -> integrations.McpServerRequest:
    return integrations.McpServerRequest(
        name=n, transport="http-sse", url=url, headers={"Authorization": token},
    )


async def test_two_orgs_register_one_name_and_get_two_rows(name) -> None:
    p, n = name
    out_a = await _as(p.org_a, lambda: integrations.register_mcp_server(
        _req(n, "https://a.example.com/mcp", "tok-a"), user=USER))
    out_b = await _as(p.org_b, lambda: integrations.register_mcp_server(
        _req(n, "https://b.example.com/mcp", "tok-b"), user=USER))
    assert out_a == out_b == {"ok": True, "name": n}
    rows = _mcp_rows(p.admin_engine, n)
    assert set(rows) == {p.org_a, p.org_b}
    assert rows[p.org_a][0] == "https://a.example.com/mcp"
    assert rows[p.org_b][0] == "https://b.example.com/mcp"
    assert rows[p.org_a][1] == {"Authorization": "tok-a"}


async def test_a_second_register_updates_only_the_callers_row(name) -> None:
    p, n = name
    await _as(p.org_a, lambda: integrations.register_mcp_server(
        _req(n, "https://a.example.com/mcp", "tok-a"), user=USER))
    await _as(p.org_b, lambda: integrations.register_mcp_server(
        _req(n, "https://b.example.com/mcp", "tok-b"), user=USER))
    await _as(p.org_a, lambda: integrations.register_mcp_server(
        _req(n, "https://a2.example.com/mcp", "tok-a2"), user=USER))
    rows = _mcp_rows(p.admin_engine, n)
    assert len(rows) == 2
    assert rows[p.org_a][:2] == ("https://a2.example.com/mcp", {"Authorization": "tok-a2"})
    assert rows[p.org_b][:2] == ("https://b.example.com/mcp", {"Authorization": "tok-b"})


async def test_no_tenant_is_refused_and_writes_no_row(name) -> None:
    p, n = name
    with pytest.raises(HTTPException) as caught:
        await _as(None, lambda: integrations.register_mcp_server(
            _req(n, "https://a.example.com/mcp", "tok"), user=USER))
    assert 400 <= caught.value.status_code < 500
    assert _mcp_rows(p.admin_engine, n) == {}


# ── Plugin install: the same fix ────────────────────────────────────────────


def _plugin_rows(admin_engine, plugin: str) -> dict[str, str]:
    with admin_engine.begin() as c:
        rows = c.execute(
            text("SELECT organization_id::text, manifest_url FROM plugins WHERE name = :n"),
            {"n": plugin},
        ).fetchall()
    return {r[0]: r[1] for r in rows}


@pytest.fixture
def plugin(graph_on_promoted, monkeypatch):  # noqa: F811
    p = graph_on_promoted
    plugin_name = f"plug_{uuid.uuid4().hex[:8]}"

    async def _fetch(method: str, url: str, **_: object) -> outbound_guard.Answer:
        body = json.dumps({"name_for_model": plugin_name, "name_for_human": "Probe"})
        return outbound_guard.Answer(status_code=200, body=body.encode())

    monkeypatch.setattr(outbound_guard, "request", _fetch)
    clear_tenant()
    try:
        yield p, plugin_name
    finally:
        clear_tenant()
        with p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM plugins WHERE name = :n"), {"n": plugin_name})


def _install(url: str):
    return integrations.install_plugin(integrations.PluginInstallRequest(manifest_url=url),
                                       user=USER)


async def test_plugin_install_per_org_and_update(plugin) -> None:
    p, plugin_name = plugin
    await _as(p.org_a, lambda: _install("https://a.example.com/ai-plugin.json"))
    await _as(p.org_b, lambda: _install("https://b.example.com/ai-plugin.json"))
    assert _plugin_rows(p.admin_engine, plugin_name) == {
        p.org_a: "https://a.example.com/ai-plugin.json",
        p.org_b: "https://b.example.com/ai-plugin.json",
    }
    await _as(p.org_a, lambda: _install("https://a2.example.com/ai-plugin.json"))
    rows = _plugin_rows(p.admin_engine, plugin_name)
    assert rows == {
        p.org_a: "https://a2.example.com/ai-plugin.json",
        p.org_b: "https://b.example.com/ai-plugin.json",
    }


async def test_plugin_install_with_no_tenant_writes_nothing(plugin) -> None:
    p, plugin_name = plugin
    with pytest.raises(HTTPException) as caught:
        await _as(None, lambda: _install("https://a.example.com/ai-plugin.json"))
    assert 400 <= caught.value.status_code < 500
    assert _plugin_rows(p.admin_engine, plugin_name) == {}
