"""Task 3866 (review): the filter of ``_tool_injection._reconnect_rows`` and the
single line for a row that the same turn marked.

Hermetic: the registry reads are faked. The R8 half is
``test_mcp_oauth_refresh_r8.py``.
"""

from __future__ import annotations

from types import SimpleNamespace as S
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="agent_framework not installed")

from gateway.connections import mcp as registry
from orchestrator import _maf_mcp, _tool_injection as ti

from tests.unit._maf_mcp_fakes import RegistrySpy, native_agent, row, set_flag

ORG = "11111111-1111-1111-1111-111111111111"


def _rows() -> list[Any]:
    return [
        S(name="allowed", agent_scope=("*",), transport="http-sse", url="https://a.example/mcp", label="A"),
        S(name="refused", agent_scope=("*",), transport="http-sse", url="https://b.example/mcp", label="B"),
        S(name="otheragent", agent_scope=("someone-else",), transport="http-sse",
          url="https://c.example/mcp", label="C"),
        S(name="stdio", agent_scope=("*",), transport="stdio", url="", label="D"),
    ]


def _fake_meta(monkeypatch: Any, rows: list[Any]) -> None:
    async def _meta(org: str) -> list[Any]:
        return rows

    monkeypatch.setattr(registry, "needs_reconnect_meta", _meta)


async def test_only_an_allowed_row_is_named(monkeypatch) -> None:
    _fake_meta(monkeypatch, _rows())
    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda org: True)
    auth = lambda svc: svc != _maf_mcp.mcp_service("refused")  # noqa: E731
    named = await ti._reconnect_rows(ORG, "mira", auth)
    assert [r.name for r in named] == ["allowed"]


async def test_no_row_is_named_with_connected_systems_off(monkeypatch) -> None:
    _fake_meta(monkeypatch, _rows())
    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda org: False)
    assert await ti._reconnect_rows(ORG, "mira", None) == []


async def test_a_read_fault_names_nothing(monkeypatch) -> None:
    async def _boom(org: str) -> list[Any]:
        raise RuntimeError("db down")

    monkeypatch.setattr(registry, "needs_reconnect_meta", _boom)
    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda org: True)
    assert await ti._reconnect_rows(ORG, "mira", None) == []


async def test_the_row_that_the_turn_marked_gets_one_line(monkeypatch) -> None:
    """The read held the row (still connected), the connect failed with
    ``reconnect``, and the metadata read now finds it parked: one line."""
    from acb_skills import server_brief

    set_flag(monkeypatch, "*")
    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda org: True)
    RegistrySpy(rows=[row("zoho", org=ORG)]).install(monkeypatch)
    _fake_meta(monkeypatch, [S(name="zoho", agent_scope=("*",), transport="http-sse",
                               url="https://mcp.example.com/mcp", label="Zoho")])

    async def _attach(agent, servers, opened, **kw):  # noqa: ANN001
        kw["failures"]["zoho"] = "reconnect"
        return []

    monkeypatch.setattr(_maf_mcp, "attach_mcp_tools", _attach)
    await ti._inject_mcp_servers(native_agent(), "mira", organization_id=ORG, opened=[])
    block = server_brief.attached_block()
    assert block.count("(zoho)") == 1, block
    assert server_brief.REASON_TEXT["reconnect"] in block
