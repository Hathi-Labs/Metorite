"""WS-8c A2 and A3 — a native MAF agent gets its OWN org's MCP servers (R8).

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c.

``mcp_servers`` is exempt from RLS, so the ``organization_id`` predicate of
``_tool_injection._MCP_ROWS_SQL`` is the whole boundary. These fences run the
REAL ``_inject_mcp_servers`` against a REAL Postgres with two organizations,
through the ``graph_on_promoted`` reroute (the non-privileged role, which
reads every row of an RLS-exempt table). Only the network is replaced
(``_maf_mcp_fakes.install_offline``), so no socket opens.

* **A2** With ``MAF_MCP_ORGS`` naming org A, and an enabled row in A and in
  B, a run bound to A carries exactly one MCP tool, from A's row. With ``*``,
  the answer is the same: the flag never widens the org. The unfiltered read
  on the same session sees both rows, so the assertion is not vacuous.
* **A3** With no org bound, the agent gets no tool and opens no session.

The payload half of A2 (an ``organization_id`` in the payload changes
nothing) is the end-to-end test in ``test_maf_mcp_injection.py``, and the AST
fence there pins the executor to the server-side stamp.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_maf_mcp_org_scope_r8.py -v -rs
"""
from __future__ import annotations

import json
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="agent_framework not installed")

import acb_graph
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from orchestrator import executor
from orchestrator._tool_injection import _inject_mcp_servers
from sqlalchemy import text

from tests.unit._maf_mcp_fakes import install_offline, native_agent, set_flag

# ``promoted`` and ``graph_on_promoted`` are fixtures, used by name. The
# imports are load-bearing although they read as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)
from tests.unit.test_mcp_servers_org_scope import graph_on_promoted  # noqa: F401

pytestmark = _DB_GATE

_AGENT = "apis-config"


def _seed(admin_engine, *, org: str, name: str) -> None:
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO mcp_servers (name, label, transport, url, headers, "
                "enabled, organization_id) VALUES (:name, :name, 'http-sse', "
                ":url, CAST(:headers AS jsonb), true, :org)"
            ),
            {"name": name, "org": org, "url": f"https://{name}.example.com/mcp",
             "headers": json.dumps({"Authorization": f"Bearer {name}"})},
        )


def _cleanup(admin_engine, names: list[str]) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM mcp_servers WHERE name = ANY(:n)"), {"n": names})


@pytest.fixture
def two_orgs(graph_on_promoted, monkeypatch):  # noqa: F811
    p = graph_on_promoted
    marker = uuid.uuid4().hex[:8]
    name_a, name_b = f"maf-a-{marker}", f"maf-b-{marker}"
    _seed(p.admin_engine, org=p.org_a, name=name_a)
    _seed(p.admin_engine, org=p.org_b, name=name_b)
    offline = install_offline(monkeypatch)
    clear_tenant()
    try:
        yield p, name_a, name_b, offline
    finally:
        clear_tenant()
        _cleanup(p.admin_engine, [name_a, name_b])


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

    # Not vacuous: an unfiltered read on the same session sees org B's row.
    with acb_graph.get_session() as s:
        every = {r[0] for r in s.execute(text("SELECT name FROM mcp_servers")).fetchall()}
    assert {name_a, name_b} <= every


async def test_the_flag_for_org_b_gives_org_a_nothing(two_orgs, monkeypatch) -> None:
    p, _name_a, _name_b, offline = two_orgs
    set_flag(monkeypatch, p.org_b)
    agent = await _inject_as(p.org_a)
    assert agent.mcp_tools == [] and offline["clients"] == []


async def test_no_org_gets_no_tool_and_reads_no_row(two_orgs, monkeypatch) -> None:
    _p, _name_a, _name_b, offline = two_orgs
    set_flag(monkeypatch, "*")
    opened: list[int] = []
    real = acb_graph.get_session

    def _counting():
        opened.append(1)
        return real()

    monkeypatch.setattr(acb_graph, "get_session", _counting)
    assert executor._current_run_org() is None  # precondition

    agent = await _inject_as(None)

    assert agent.mcp_tools == [] and offline["clients"] == []
    assert opened == [], "a run with no org opened a registry session"
