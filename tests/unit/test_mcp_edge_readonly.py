"""The read-only door of the edge, ``/mcp/readonly`` (SW-34b). R7: this file
is the fence.

Spec ``project-docs/specs/agent_coordination.md`` SW-34b. The edge serves a
second path. For each call there, it narrows the caller's scopes to the read
scopes (``agent_oauth.read_only``) right after the bearer check. So
``tools/list`` and check 8 of ``mcp.py`` do the work, and no second scope
rule exists. The same token works on both paths, and the path can only
narrow it.

Database-free, through the real edge app, with the fake store and the fake
gateway of ``test_mcp_edge_protocol.py``. Each flag that shows a write tool
is on: the writes flag, the runs flag and the door of Knowledge.

**The mutations (R7).** Each one turned the named tests red. ``E`` is
``apps/services/agent_edge/agent_edge``.

* R1. ``E/mcp.py`` ``_serve``: no narrowing on the read-only path.
  ``test_each_write_tool_is_refused_on_the_readonly_path``,
  ``test_the_readonly_list_holds_only_reads`` and four more.
* R2. ``E/mcp.py``: narrow only for ``tools/list``, and run check 8 on the
  full scopes. ``test_each_write_tool_is_refused_on_the_readonly_path``.
* R3. ``acb_auth/agent_oauth.py`` ``READ_SCOPES``: holds ``projects:write``.
  ``test_read_only_never_keeps_a_write_scope`` and the walk.
* R4. ``E/tools.py`` ``_covered``: no write-scope check for ``app_write``.
  ``test_the_readonly_list_holds_only_reads`` and
  ``test_app_write_hides_from_a_connection_with_no_write_scope``.
"""
from __future__ import annotations

import itertools
import json
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth
from acb_auth.agent_oauth import MCP_WRITE_ROUTES
from acb_common import get_settings
from agent_edge import app as edge_app_module
from agent_edge import mcp, tools

from tests.unit.test_mcp_edge_protocol import (  # noqa: F401 — fixtures
    GOOD,
    ISSUER,
    _call,
    _lookup,
    _rpc,
    armed,
    gateway,
    store,
)

READONLY = "/mcp/readonly"
NORMAL = "/mcp"

#: A token and a connection that hold every write scope of the walk.
FULL = "full-token"
FULL_SCOPES = ("projects:write", "context:read", "knowledge:write")

#: The same reach with the read scopes only.
READ = "read-token"
READ_SCOPES = ("projects:read", "context:read", "knowledge:read")

DOOR_APP = "knowledge"


def _writes_of(tool: tools.Tool) -> set[tuple[str, str]]:
    """The pairs of ``MCP_WRITE_ROUTES`` that *tool* may send."""
    pairs = {(tool.method, r) for r in (tool.route, *tool.routes, *tool.prior_writes)}
    pairs |= set(tool.alt_writes)
    return pairs & MCP_WRITE_ROUTES


def _is_write(tool: tools.Tool) -> bool:
    """A tool that sends a write pair, or whose scope is a write scope."""
    scope = tool.required_scope
    return (bool(_writes_of(tool))
            or (scope is not None and agent_oauth.covered_by(scope) is not None)
            or tool.app_scope == "write")


#: Every write tool of the edge, in the order of ``tools/list``.
WRITE_TOOLS = [t.name for t in tools.TOOLS.values() if _is_write(t)]


def _arguments(name: str) -> dict[str, Any]:
    """The door write names its app, so check 8 knows its scope. Check 8
    runs before the input check, so no other tool needs an argument."""
    return {"app": DOOR_APP, "op": "x", "body": {}} if name == "app_write" else {}


@pytest.fixture
def open_edge(armed, monkeypatch):  # noqa: F811 — a fixture
    """Every flag that shows a write tool, and the two tokens."""
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_access_writes_enabled", True)
    monkeypatch.setattr(settings, "agent_runs_enabled", True)
    monkeypatch.setattr(settings, "agent_door_apps", DOOR_APP)
    armed.store.tokens[FULL] = _lookup(scope=" ".join(FULL_SCOPES), scopes=FULL_SCOPES)
    armed.store.tokens[READ] = _lookup(scope=" ".join(READ_SCOPES), scopes=READ_SCOPES)
    return armed


async def _post(path: str, body: Any, *, token: str | None = FULL,
                headers: dict[str, str] | None = None,
                method: str = "POST") -> httpx.Response:
    sent = {"content-type": "application/json", "mcp-protocol-version": "2025-11-25"}
    if token is not None:
        sent["authorization"] = f"Bearer {token}"
    sent.update(headers or {})
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=edge_app_module.create_app()),
            base_url=ISSUER) as client:
        return await client.request(method, path, content=json.dumps(body), headers=sent)


async def _result(path: str, method: str, params: dict[str, Any] | None = None,
                  token: str = FULL) -> dict[str, Any]:
    r = await _post(path, _rpc(method, params), token=token)
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def _names(path: str, token: str = FULL) -> list[str]:
    return [t["name"] for t in (await _result(path, "tools/list", token=token))["tools"]]


class TestTheWalk:
    """Done when: a call of each write tool gets 403 at the edge."""

    def test_the_walk_covers_every_write_route(self):
        walked = set().union(*(_writes_of(tools.TOOLS[n]) for n in WRITE_TOOLS))
        assert walked == set(MCP_WRITE_ROUTES), (
            "a pair of MCP_WRITE_ROUTES has no write tool in the walk")
        assert "app_write" in WRITE_TOOLS

    @pytest.mark.parametrize("name", WRITE_TOOLS)
    async def test_each_write_tool_is_refused_on_the_readonly_path(self, open_edge, name):
        tool = tools.TOOLS[name]
        assert tools.offered(tool), "the walk needs each write tool offered"
        r = await _post(READONLY, _call(name, _arguments(name)))
        assert r.status_code == 403, r.text
        challenge = r.headers["www-authenticate"]
        assert 'error="insufficient_scope"' in challenge
        assert f'scope="{tools.scope_of(tool, _arguments(name))}"' in challenge
        assert open_edge.gateway.requests == [], "a refused write reached the gateway"

    @pytest.mark.parametrize("name", WRITE_TOOLS)
    async def test_the_same_token_passes_check_8_on_the_normal_path(self, open_edge, name):
        r = await _post(NORMAL, _call(name, _arguments(name)))
        assert r.status_code == 200, r.text


class TestTheList:
    """Done when: a connection through the read-only path lists only reads."""

    async def test_the_readonly_list_holds_only_reads(self, open_edge):
        names = await _names(READONLY)
        assert names, "the read-only path lists no tool"
        assert not set(names) & set(WRITE_TOOLS)
        for name in names:
            scope = tools.TOOLS[name].required_scope
            assert scope is None or agent_oauth.covered_by(scope) is None

    async def test_the_readonly_list_is_the_list_of_a_read_token(self, open_edge):
        assert await _names(READONLY) == await _names(NORMAL, token=READ)

    async def test_the_full_token_still_lists_the_write_tools_on_the_normal_path(self, open_edge):
        names = set(await _names(NORMAL))
        expected = {n for n in WRITE_TOOLS if not tools.TOOLS[n].switch}
        assert expected <= names
        assert {"claim_task", "create_task", "app_write"} <= names

    async def test_a_read_token_lists_the_same_on_both_paths(self, open_edge):
        assert await _names(READONLY, token=READ) == await _names(NORMAL, token=READ)

    async def test_app_write_hides_from_a_connection_with_no_write_scope(self, open_edge):
        """``app_write`` has no fixed scope, so ``tools._covered`` hides it
        when the scopes hold no write scope. ``app_read`` still shows."""
        names = await _names(NORMAL, token=READ)
        assert "app_write" not in names
        assert "app_read" in names


class TestEverySurfaceFollowsTheDoor:

    async def test_whoami_names_the_read_scopes_only(self, open_edge):
        result = await _result(READONLY, "tools/call", {"name": "whoami", "arguments": {}})
        assert result["structuredContent"]["scopes"] == sorted(READ_SCOPES)

    async def test_the_prompt_hides_on_the_readonly_path(self, open_edge):
        assert (await _result(NORMAL, "prompts/list"))["prompts"]
        assert (await _result(READONLY, "prompts/list"))["prompts"] == []

    async def test_the_instructions_name_no_write_tool(self, open_edge):
        params = {"protocolVersion": "2025-11-25"}
        readonly = (await _result(READONLY, "initialize", params))["instructions"]
        assert readonly == (await _result(NORMAL, "initialize", params, token=READ))["instructions"]
        assert "claim_task" in (await _result(NORMAL, "initialize", params))["instructions"]
        assert "claim_task" not in readonly


class TestTheTransport:

    async def test_no_token_is_401_on_the_readonly_path(self, open_edge):
        r = await _post(READONLY, _rpc("tools/list"), token=None)
        assert r.status_code == 401
        assert "resource_metadata=" in r.headers["www-authenticate"]

    async def test_a_foreign_origin_is_403_on_the_readonly_path(self, open_edge):
        r = await _post(READONLY, _rpc("tools/list"),
                        headers={"origin": "https://evil.example"})
        assert r.status_code == 403
        assert "www-authenticate" not in r.headers

    async def test_a_wrong_method_is_405_on_the_readonly_path(self, open_edge):
        r = await _post(READONLY, _rpc("tools/list"), method="GET")
        assert r.status_code == 405

    async def test_the_flag_off_is_404_on_the_readonly_path(self, open_edge, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_enabled", False)
        r = await _post(READONLY, _rpc("tools/list"))
        assert r.status_code == 404

    def test_the_readonly_path_sits_below_the_resource(self, open_edge):
        assert mcp.readonly_path() == READONLY


class TestTheRule:

    def test_read_only_never_keeps_a_write_scope(self):
        assert agent_oauth.PROJECTS_WRITE not in agent_oauth.READ_SCOPES
        assert all(agent_oauth.covered_by(s) is None for s in agent_oauth.READ_SCOPES)
        assert agent_oauth.PROJECTS_READ in agent_oauth.READ_SCOPES

    def test_read_only_never_widens(self):
        scopes = agent_oauth.RESOURCE_SCOPES
        for size in range(len(scopes) + 1):
            for held in itertools.combinations(scopes, size):
                narrowed = agent_oauth.read_only(held)
                assert narrowed <= set(held)
                assert not any(agent_oauth.covered_by(s) for s in narrowed)
