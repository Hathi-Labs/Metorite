"""WS-8c A2 and A3 — a native MAF agent gets its OWN org's MCP servers (R8).

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c.

WS-54 IN-4 moved the servers into the connection registry. It has FORCE
RLS, and the one reader ``gateway.connections.mcp.servers_for_org`` opens
``tenant_session(org)``. These fences run the REAL ``_inject_mcp_servers``
against a REAL Postgres with two organizations, through the
``registry_on_promoted`` reroute (the non-privileged role). The servers are
seeded through the real writer, with an encrypted header. Only the network
is replaced (``_maf_mcp_fakes.install_offline``), so no socket opens.

* **A2** With ``MAF_MCP_ORGS`` naming org A, and an enabled row in A and in
  B, a run bound to A carries exactly one MCP tool, from A's row. With ``*``,
  the answer is the same: the flag never widens the org. The admin read
  (no RLS) sees both rows, so the assertion is not vacuous.
* **A3** With no org bound, the agent gets no tool and opens no session.

The payload half of A2 (an ``organization_id`` in the payload changes
nothing) is the end-to-end test in ``test_maf_mcp_injection.py``, and the AST
fence there pins the executor to the server-side stamp.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_maf_mcp_org_scope_r8.py -v -rs
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="agent_framework not installed")

from acb_common.db import bind_tenant, clear_tenant, release_tenant
from orchestrator import executor
from orchestrator._tool_injection import _inject_mcp_servers

from tests.unit._maf_mcp_fakes import install_offline, native_agent, set_flag

# ``promoted``, ``graph_on_promoted`` and ``registry_on_promoted`` are
# fixtures, used by name. The imports are load-bearing although they read as
# unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_mcp_servers_org_scope import (  # noqa: F401
    graph_on_promoted,
    registry_names,
    registry_on_promoted,
    seed_registry,
)

pytestmark = _DB_GATE

_AGENT = "apis-config"


async def _seed(*, org: str, name: str) -> None:
    await seed_registry(
        org, name, url=f"https://{name}.example.com/mcp",
        headers={"Authorization": f"Bearer {name}"},
    )


@pytest.fixture
async def two_orgs(registry_on_promoted, monkeypatch):  # noqa: F811
    p = registry_on_promoted
    marker = uuid.uuid4().hex[:8]
    name_a, name_b = f"maf-a-{marker}", f"maf-b-{marker}"
    await _seed(org=p.org_a, name=name_a)
    await _seed(org=p.org_b, name=name_b)
    offline = install_offline(monkeypatch)
    clear_tenant()
    try:
        yield p, name_a, name_b, offline
    finally:
        clear_tenant()


async def _inject_as(org: str | None) -> object:
    agent = native_agent()
    token = bind_tenant(org) if org else clear_tenant()
    try:
        await _inject_mcp_servers(agent, _AGENT, opened=[])
    finally:
        release_tenant(token)
    return agent


@pytest.mark.parametrize("flag", ["org-a", "*"])
async def test_a_run_in_org_a_carries_only_org_a_server(two_orgs, monkeypatch, flag) -> None:
    p, name_a, name_b, offline = two_orgs
    set_flag(monkeypatch, p.org_a if flag == "org-a" else "*")

    agent = await _inject_as(p.org_a)

    names = [t.name for t in agent.mcp_tools]
    assert names == [name_a], f"org A's run got {names}"
    assert [c.url for c in offline["clients"]] == [f"https://{name_a}.example.com/mcp"]
    assert agent.mcp_tools[0]._static_headers == {"Authorization": f"Bearer {name_a}"}

    # Not vacuous: the admin read (no RLS, no filter) sees org B's row.
    assert {name_a, name_b} <= registry_names(p.admin_engine)


async def test_the_flag_for_org_b_gives_org_a_nothing(two_orgs, monkeypatch) -> None:
    p, _name_a, _name_b, offline = two_orgs
    set_flag(monkeypatch, p.org_b)
    agent = await _inject_as(p.org_a)
    assert agent.mcp_tools == [] and offline["clients"] == []


async def test_no_org_gets_no_tool_and_reads_no_row(two_orgs, monkeypatch) -> None:
    _p, _name_a, _name_b, offline = two_orgs
    set_flag(monkeypatch, "*")
    from gateway.connections import mcp

    opened: list[int] = []
    real = mcp.tenant_session

    def _counting(*args, **kwargs):
        opened.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(mcp, "tenant_session", _counting)
    assert executor._current_run_org() is None  # precondition

    agent = await _inject_as(None)

    assert agent.mcp_tools == [] and offline["clients"] == []
    assert opened == [], "a run with no org opened a registry session"
