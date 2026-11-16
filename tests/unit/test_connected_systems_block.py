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
    assert "Ok (ok)" in text and "Tools: search" in text
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


# ── CSY-5: a turn that cannot reach a server says why ───────────────────────


def _rows() -> list[Any]:
    return [SimpleNamespace(name="ok", label="Ok App", description="d", transport="http-sse",
                            url="https://ok.example/mcp", agent_scope="all", headers={}),
            SimpleNamespace(name="refused", label="Secret App", description="d", transport="http-sse",
                            url="https://r.example/mcp", agent_scope="all", headers={})]


async def test_a_mail_run_names_the_servers_it_could_use_and_never_a_refused_one(monkeypatch) -> None:
    import gateway.connections.mcp as registry

    calls: list[str] = []

    async def enabled_meta(org: str) -> list[Any]:
        calls.append(org)
        return _rows()

    monkeypatch.setattr(registry, "enabled_meta", enabled_meta)
    monkeypatch.setattr(_maf_mcp, "is_native_maf_agent", lambda a: True)
    monkeypatch.setattr(_maf_mcp, "maf_mcp_org_allowed", lambda o: True)
    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda o: True)
    monkeypatch.setattr(_tool_injection, "_keep_mcp_server", lambda s, a: True)
    await _tool_injection._note_mail_skipped(
        object(), "orchestrator", "org-a", lambda svc: svc != "mcp-refused")
    text = sb.attached_block()
    assert calls == ["org-a"]
    assert "Ok App (ok): " + sb.REASON_TEXT["mail"] in text
    assert "refused" not in text and "Secret App" not in text
    assert sb.STANDING_RULE not in text


async def test_a_mail_run_reads_nothing_with_the_flag_off(monkeypatch) -> None:
    import gateway.connections.mcp as registry

    async def boom(org: str) -> list[Any]:
        raise AssertionError("the registry was read with the flag off")

    monkeypatch.setattr(registry, "enabled_meta", boom)
    monkeypatch.setattr(_maf_mcp, "is_native_maf_agent", lambda a: True)
    monkeypatch.setattr(_maf_mcp, "maf_mcp_org_allowed", lambda o: True)
    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda o: False)
    await _tool_injection._note_mail_skipped(object(), "orchestrator", "org-a", None)
    assert sb.attached_block() == ""


def _server(name: str, **kw: Any) -> Any:
    return _maf_mcp.McpServer(name=name, url="https://x.example/mcp", label=name.title(), **kw)


def _tool(name: str, hints: dict | None) -> Any:
    t = type("T", (), {})()
    t.name = name
    t.__dict__["_ws8c_tool_meta"] = {"op": _maf_mcp.ToolMeta(hints=hints, description="")}
    return t


async def test_the_reasons_cover_unreachable_reconnect_no_card_and_asks_each_call(monkeypatch) -> None:
    import acb_skills.egress as egress

    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda o: True)

    async def marked() -> bool:
        return True

    monkeypatch.setattr(egress, "session_is_marked", marked)
    servers = [_server("reads"), _server("writes")]
    tools = [_tool("reads", {"readOnlyHint": True}), _tool("writes", {"destructiveHint": True})]
    out = await _tool_injection._mcp_reasons(
        "org-a", servers, ["reads", "writes"], {"dead": "unreachable", "oauth": "reconnect", "x": "weird"},
        tools, member_present=True)
    # The chat read mail: the read-only tool now asks a card for each call. A write tool asks a
    # card anyway, so the mark adds nothing there.
    assert out == {"dead": "unreachable", "oauth": "reconnect", "reads": "asks_each_call"}
    assert set(out.values()) <= set(sb.REASONS)

    async def clean() -> bool:
        return False

    monkeypatch.setattr(egress, "session_is_marked", clean)
    out = await _tool_injection._mcp_reasons("org-a", servers, ["reads", "writes"], {}, tools, member_present=False)
    assert out == {"writes": "no_card"}  # the read-only tool still runs with no member


async def test_the_reasons_read_nothing_with_the_flag_off(monkeypatch) -> None:
    monkeypatch.setattr(_maf_mcp, "connected_systems_org_allowed", lambda o: False)
    assert await _tool_injection._mcp_reasons("o", [], [], {"a": "unreachable"}, None, True) == {}


def test_a_note_line_follows_the_briefs_and_a_refused_server_is_not_in_the_rows() -> None:
    rows = [SimpleNamespace(name="ok", label="Ok", description="d"),
            SimpleNamespace(name="down", label="Down", description="d")]
    tool = _tool("ok", {"readOnlyHint": True})
    _tool_injection._note_mcp_briefs(rows, ["ok"], [tool], {"down": "unreachable"})
    text = sb.attached_block()
    assert text.index("- Ok (ok)") < text.index("- Down (down): ") < text.index(sb.STANDING_RULE[:20])


async def test_attach_reports_a_refused_server_as_unreachable(monkeypatch) -> None:
    from gateway import outbound_guard

    async def refuse(url: str) -> Any:
        raise outbound_guard.OutboundRefused("x.example", "blocked")

    monkeypatch.setattr(outbound_guard, "mcp_client", refuse)
    failures: dict[str, str] = {}
    agent = SimpleNamespace(mcp_tools=[], middleware=[])
    got = await _maf_mcp.attach_mcp_tools(agent, [_server("down")], [], failures=failures)
    assert got == [] and failures == {"down": "unreachable"}
