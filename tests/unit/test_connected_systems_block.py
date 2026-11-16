"""CSY-1 (task 3400): the per-turn "Connected systems" block.

Spec: ``project-docs/specs/chat_connected_systems.md`` §3.2. Acceptance:

1. With a server attached and the flag on, the per-turn context ends with the block.
2. With no server, or the flag off, the context is byte-identical to today.
3. A server that ``is_authorized`` refuses never shows.
4. The same attached set gives the same bytes.
5. The stable persona and the pack catalog do not change (no edit to them).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from acb_skills import server_brief as sb

pytest.importorskip("agent_framework", reason="agent_framework not installed")

from orchestrator import _maf_mcp, _tool_injection, executor


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    sb.set_attached(())
    monkeypatch.setattr(executor, "_current_run_org", lambda: "org-a")
    yield
    sb.set_attached(())


def _flag(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda org: on and org == "org-a")


def _parts() -> list[str]:
    return executor._context_preamble_parts({}, {}, "")


def _attach(*names: str) -> None:
    sb.set_attached((n, sb.server_brief(n.title(), n, f"{n} data", ["search"])) for n in names)


def test_with_a_server_and_the_flag_on_the_context_ends_with_the_block(monkeypatch) -> None:
    _flag(monkeypatch, True)
    _attach("shaurrya-app")
    parts = _parts()
    assert parts[-1].startswith(sb.BLOCK_HEADER) and parts[-1].endswith(sb.STANDING_RULE)
    assert "Shaurrya-App (shaurrya-app)" in parts[-1]


def test_with_the_flag_off_the_context_is_byte_identical(monkeypatch) -> None:
    _flag(monkeypatch, False)
    before = _parts()
    _attach("shaurrya-app")
    assert _parts() == before


def test_with_no_server_the_context_is_byte_identical(monkeypatch) -> None:
    _flag(monkeypatch, True)
    none_attached = _parts()
    _flag(monkeypatch, False)
    assert _parts() == none_attached
    assert not any(p.startswith(sb.BLOCK_HEADER) for p in none_attached)


def test_the_same_set_gives_the_same_bytes_in_any_attach_order(monkeypatch) -> None:
    _flag(monkeypatch, True)
    _attach("beta", "alpha")
    first = _parts()
    _attach("alpha", "beta")
    assert _parts() == first
    assert first[-1].index("alpha") < first[-1].index("beta")


def test_the_rule_says_connected_system_and_keys_on_the_data_source() -> None:
    assert "Use the Metorite tools that you hold first for Metorite's own records" in sb.STANDING_RULE
    assert "server" not in sb.STANDING_RULE.lower()


def test_the_real_flag_reader_reads_the_run_org_and_star(monkeypatch) -> None:
    def settings(v: str) -> Any:
        return SimpleNamespace(connected_systems_orgs=v)

    monkeypatch.setattr(_maf_mcp, "get_settings", lambda: settings("org-a, ORG-B"))
    assert _maf_mcp.connected_systems_org_allowed("org-b") and not _maf_mcp.connected_systems_org_allowed("org-c")
    monkeypatch.setattr(_maf_mcp, "get_settings", lambda: settings("*"))
    assert _maf_mcp.connected_systems_org_allowed("org-c") and not _maf_mcp.connected_systems_org_allowed("")
    monkeypatch.setattr(_maf_mcp, "get_settings", lambda: settings(""))
    assert not _maf_mcp.connected_systems_org_allowed("org-a")


def test_only_a_server_that_attached_gets_a_brief() -> None:
    """A server that ``is_authorized`` refuses never reaches ``attach_mcp_tools``, and one that
    fails to connect is not in its answer, so neither shows."""
    rows = [SimpleNamespace(name="ok", label="Ok", description="fine"),
            SimpleNamespace(name="refused", label="No", description="hidden")]
    tool = type("T", (), {})()
    tool.name = "ok"
    tool.__dict__["_ws8c_tool_meta"] = {"search": object()}
    _tool_injection._note_mcp_briefs(rows, ["ok"], [tool])
    text = sb.attached_block()
    assert "Ok (ok)" in text and "_<tool>): search" in text
    assert "refused" not in text and "hidden" not in text


def test_the_next_run_does_not_keep_an_old_brief() -> None:
    _attach("old")
    _tool_injection._note_mcp_briefs([], [], None)
    assert sb.attached_block() == ""


def test_maf_servers_carry_the_label_and_the_description() -> None:
    srv = SimpleNamespace(name="s", transport="http-sse", url="https://x.example/mcp", headers={},
                          label="S App", description="Holds S data")
    (out,) = _tool_injection._maf_servers([srv], None)
    assert out.label == "S App" and out.description == "Holds S data"


def test_a_delegated_run_does_not_read_the_parents_briefs(monkeypatch) -> None:
    """A delegated run copies its parent's context and holds no MCP tool (review of PR 2497)."""
    _flag(monkeypatch, True)
    structlog.contextvars.clear_contextvars()
    try:
        structlog.contextvars.bind_contextvars(run_id="parent")
        _attach("shaurrya-app")
        assert sb.attached_block() != ""
        structlog.contextvars.bind_contextvars(run_id="child")
        assert sb.attached_block() == ""
        assert not any(p.startswith(sb.BLOCK_HEADER) for p in _parts())
        structlog.contextvars.bind_contextvars(run_id="parent")
        assert sb.attached_block() != ""
    finally:
        structlog.contextvars.clear_contextvars()


def test_a_run_with_no_bound_id_still_reads_its_briefs() -> None:
    structlog.contextvars.clear_contextvars()
    _attach("s")
    assert sb.attached_block() != ""
