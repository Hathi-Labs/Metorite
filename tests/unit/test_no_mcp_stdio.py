"""WS-8c A7 — Metorite runs no MCP server over stdio (D85).

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c, acceptance A7.

A stdio MCP server is a process that the host starts. D85 says a shared agent
runs no code on the host until the sandbox covers it. So:

* No ``MCPStdioTool(`` call exists in ``apps/`` or ``packages/`` (an AST walk,
  which a planted call proves can fail).
* ``POST /integrations/mcp`` answers 422 for stdio and writes nothing.
* ``POST /integrations/mcp/test`` answers 422 for stdio and does not probe
  the PATH of the host.
* Both injection paths, Copilot and MAF, drop a stdio row and log
  ``mcp.stdio_refused``.
"""
from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest
import structlog

pytest.importorskip("agent_framework", reason="agent_framework not installed")

from acb_auth import UserContext, UserRole
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from fastapi import HTTPException
from gateway.routes import integrations
from orchestrator._tool_injection import _inject_mcp_servers

from tests.unit._maf_mcp_fakes import (
    CopilotStub,
    RegistrySpy,
    native_agent,
    refuse_registry,
    row,
    set_flag,
)

REPO = Path(__file__).resolve().parents[2]
ORG = "cccccccc-0000-0000-0000-00000000000c"
USER = UserContext(email="admin@example.test", role=UserRole.EXECUTIVE, organization_id=ORG)
_SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git"}


def _stdio_calls(source: str) -> list[int]:
    """Line numbers of each call whose callee is named ``MCPStdioTool``."""
    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        if name == "MCPStdioTool":
            hits.append(node.lineno)
    return hits


def _python_files() -> list[Path]:
    out: list[Path] = []
    for root in ("apps", "packages"):
        for path in (REPO / root).rglob("*.py"):
            if not _SKIP_DIRS.intersection(path.parts):
                out.append(path)
    return out


def test_no_code_builds_an_mcp_stdio_tool() -> None:
    files = _python_files()
    assert len(files) > 200, "the walk saw too few files to mean anything"
    found = {
        str(p.relative_to(REPO)): lines
        for p in files
        if "MCPStdioTool" in (src := p.read_text(encoding="utf-8", errors="replace"))
        and (lines := _stdio_calls(src))
    }
    assert found == {}, f"D85: an MCP stdio server starts a host process: {found}"


@pytest.mark.parametrize("planted", [
    "from agent_framework import MCPStdioTool\nt = MCPStdioTool(name='x', command='npx')\n",
    "import agent_framework as af\nt = af.MCPStdioTool(name='x', command='npx')\n",
])
def test_the_fence_can_fail(planted: str) -> None:
    assert _stdio_calls(planted) == [2]


# ── The routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def tenant():
    clear_tenant()
    token = bind_tenant(ORG)
    try:
        yield ORG
    finally:
        release_tenant(token)
        clear_tenant()


@pytest.fixture
def no_db(monkeypatch):
    # IN-4: the registry reader is the only way to the store.
    refuse_registry(monkeypatch, "a stdio request reached the database")


@pytest.mark.usefixtures("tenant", "no_db")
async def test_post_mcp_refuses_stdio_with_422() -> None:
    req = integrations.McpServerRequest(name="local-io", transport="stdio", command="npx -y srv")
    with pytest.raises(HTTPException) as caught:
        await integrations.register_mcp_server(req, user=USER)
    assert caught.value.status_code == 422
    assert "stdio" in caught.value.detail


@pytest.mark.usefixtures("tenant")
async def test_mcp_test_refuses_stdio_and_probes_no_path(monkeypatch) -> None:
    probed: list[str] = []
    monkeypatch.setattr(shutil, "which", lambda exe, *a, **k: probed.append(exe) or "/bin/x")
    req = integrations.McpServerRequest(name="local-io", transport="stdio", command="bash -c id")
    with pytest.raises(HTTPException) as caught:
        await integrations.test_mcp_server(req, user=USER)
    assert caught.value.status_code == 422
    assert probed == [], "the test route probed the PATH of the host"


# ── The injection paths ──────────────────────────────────────────────────────


@pytest.mark.parametrize("make", [native_agent, CopilotStub], ids=["maf", "copilot"])
async def test_both_injection_paths_drop_a_stdio_row(make, monkeypatch) -> None:
    set_flag(monkeypatch, "*")
    RegistrySpy([row("local-io", org=ORG, transport="stdio", url="")]).install(monkeypatch)
    agent = make()
    with structlog.testing.capture_logs() as logs:
        await _inject_mcp_servers(agent, "apis-config", organization_id=ORG, opened=[])
    assert getattr(agent, "mcp_tools", []) == []
    assert getattr(agent, "_default_options", {}) == {}
    assert {"event": "mcp.stdio_refused", "name": "local-io", "log_level": "warning"} in logs
