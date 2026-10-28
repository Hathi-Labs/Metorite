"""A large tool result as a preview and a handle (CTX-1, Metorite #825).

Spec: ``project-docs/specs/one_assistant_context.md`` §7.1.

A scripted model runs inside a real MAF ``Agent`` with its real tool loop and
its real middleware pipeline, as ``test_crm_answer_check.py`` does. The cases
follow the acceptance list of §7.1:

1. ``off`` changes nothing                     -> ``TestOff``
2. ``shadow`` logs sizes and changes nothing   -> ``TestShadow``
3. ``on`` cuts a 100K result to a preview      -> ``TestOn``
4. the read tool pages, and keys by run        -> ``TestTheReadTool``
5. a framed result stays framed and held       -> ``TestUntrusted``
6. no lasting row                              -> ``test_the_module_writes_no_lasting_row``
7. a CRM result above the cap feeds the check  -> ``TestTheNumberCheck``
8. exempt tools never spill                    -> ``TestExempt``
9. the first 2000 chars do not change          -> ``test_the_head_is_exact``
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from acb_skills import tool_result_spill as trs
from acb_skills.answer_check import ON, AnswerCheckProvider
from acb_skills.tool_result_spill import (
    NOT_FOUND,
    SpillToolResults,
    ToolResultSpillProvider,
    preview,
    read_tool_result,
)
from agent_framework import Agent, FunctionTool

from tests.unit.test_crm_answer_check import ScriptedClient

ROOT = Path(__file__).resolve().parents[2]
SPILL = 24000
PREVIEW = 4000
BIG = 100_000


def _big(n: int = BIG) -> str:
    """*n* chars of numbered lines, each 20 chars long."""
    line = "row {:06d} of the list\n"
    out = "".join(line.format(i) for i in range(n // 23 + 1))
    return out[0:n]


async def fetch_rows(page: int = 1) -> str:
    """A fake tool that returns a 100K result."""
    return _big()


async def fetch_small(page: int = 1) -> str:
    """A fake tool that returns a short result."""
    return "three rows"


def _mcp_tool() -> FunctionTool:
    async def mcp_page() -> str:
        return _big()

    return FunctionTool(func=mcp_page, name="mcp_page", description="An MCP stub.",
                        additional_properties={"_mcp_is_tool": True})


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from acb_skills import egress

    trs.clear_store()
    egress._MARKED_SESSIONS.clear()

    async def _clear() -> bool:
        return False

    monkeypatch.setattr(trs, "session_is_marked", _clear)
    yield
    trs.clear_store()
    egress._MARKED_SESSIONS.clear()


@pytest.fixture
def as_run():
    """Bind a tenant, a member and a thread, the way a run does."""
    from acb_common import bind_run_context
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import bind_artifact_context

    held: list[Any] = []

    def _bind(org: str = "org-a", member: str = "ann@a.test", thread: str = "t-1") -> None:
        if held:
            release_tenant(held.pop())
        held.append(bind_tenant(org))
        bind_run_context(user=member, thread_id=thread, member_verified=True)
        bind_artifact_context(session_id=thread, member=member)

    yield _bind
    if held:
        release_tenant(held.pop())
    bind_artifact_context()


def _agent(script, tools=None):
    client = ScriptedClient(script)
    agent = Agent(client=client, name="orchestrator",
                  tools=tools if tools is not None else [fetch_rows, fetch_small, read_tool_result])
    return agent, client


def _view(agent: Any, mode: str, *more: Any) -> Any:
    from orchestrator._native_run_context import agent_with_providers

    providers: list[Any] = [] if mode == "off" else [
        ToolResultSpillProvider(mode, SPILL, PREVIEW)]
    return agent_with_providers(agent, [*providers, *more])


def _sizes(client: ScriptedClient) -> list[int]:
    """The chars a request carries: message text and tool results."""
    sizes = []
    for req in client.requests:
        total = 0
        for message in req:
            for content in message.contents:
                total += len(content.text or "") if content.type == "text" else 0
                if content.type == "function_result":
                    total += len(str(content.result))
        sizes.append(total)
    return sizes


def _tool_texts(request) -> list[str]:
    out: list[str] = []
    for message in request:
        for content in message.contents:
            if content.type == "function_result":
                result = content.result
                out.append(result if isinstance(result, str) else str(result))
    return out


@pytest.fixture
def logged(monkeypatch):
    lines: list[dict[str, Any]] = []
    monkeypatch.setattr(trs._log, "info", lambda event, **kw: lines.append({"event": event, **kw}))
    return lines


SCRIPT = [("call", "fetch_rows", {}), ("call", "fetch_small", {}), ("text", "Done.")]


# ── The setting ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("raw", "mode"), [
    ("off", "off"), ("shadow", "shadow"), ("ON", "on"), (" on ", "on"),
    ("yes", "off"), ("", "off"),
])
def test_the_mode_reads_the_setting(raw: str, mode: str, monkeypatch) -> None:
    from acb_common import get_settings
    from acb_common.settings import tool_result_spill_mode

    monkeypatch.setattr(get_settings(), "tool_result_spill", raw)
    assert tool_result_spill_mode() == mode


def test_the_defaults_ship_dark() -> None:
    from acb_common.settings import (
        Settings,
        tool_result_preview_chars,
        tool_result_spill_chars,
    )

    fields = Settings.model_fields
    assert fields["tool_result_spill"].default == "off"
    assert fields["tool_result_spill_chars"].default == 24000
    assert fields["tool_result_preview_chars"].default == 4000
    assert tool_result_spill_chars() > 0 and tool_result_preview_chars() > 0


# ── 1. off ───────────────────────────────────────────────────────────────────

class TestOff:
    def test_the_injection_adds_no_provider_and_no_tool(self, monkeypatch) -> None:
        from orchestrator import _tool_injection as ti

        agent, _ = _agent([])
        agents: list[Any] = [agent]
        ti._apply_tool_result_spill(agents, "orchestrator")
        assert agents[0] is agent and not agent.context_providers

    async def test_the_request_bytes_do_not_change(self, as_run) -> None:
        as_run()
        plain, plain_client = _agent(list(SCRIPT))
        await plain.run("go")
        view_agent, view_client = _agent(list(SCRIPT))
        await _view(view_agent, "off").run("go")
        assert _sizes(plain_client) == _sizes(view_client)

    def test_the_orchestrator_gains_no_tool(self, monkeypatch) -> None:
        names = _orchestrator_tools(monkeypatch, "off")
        assert "read_tool_result" not in names


# ── 2. shadow ────────────────────────────────────────────────────────────────

class TestShadow:
    async def test_it_logs_one_line_for_each_result_and_no_text(self, as_run, logged) -> None:
        as_run()
        agent, _ = _agent(list(SCRIPT))
        await _view(agent, "shadow").run("go")
        lines = [e for e in logged if e["event"] == "tool_result.size"]
        assert [e["tool"] for e in lines] == ["fetch_rows", "fetch_small"]
        big = lines[0]
        assert big["chars"] == BIG and big["tokens"] == BIG // 4
        assert big["source"] == "core" and big["would_spill"] is True
        assert lines[1]["would_spill"] is False
        for entry in lines:
            assert set(entry) == {"event", "tool", "chars", "tokens", "source",
                                  "untrusted", "would_spill", "oversize", "mode"}
        assert "row 0000" not in json.dumps(logged)

    async def test_the_request_bytes_do_not_change(self, as_run) -> None:
        as_run()
        plain, plain_client = _agent(list(SCRIPT))
        await plain.run("go")
        agent, client = _agent(list(SCRIPT))
        await _view(agent, "shadow").run("go")
        assert _sizes(plain_client) == _sizes(client)
        assert trs._STORE == {}

    def test_the_injection_attaches_it_to_a_native_agent(self, monkeypatch) -> None:
        from acb_common import get_settings
        from orchestrator import _tool_injection as ti

        monkeypatch.setattr(get_settings(), "tool_result_spill", "shadow")
        agent, _ = _agent([], tools=[fetch_small])
        agents: list[Any] = [agent]
        ti._apply_tool_result_spill(agents, "x")
        assert [type(p) for p in agents[0].context_providers] == [ToolResultSpillProvider]


# ── 3. on ────────────────────────────────────────────────────────────────────

class TestOn:
    async def test_an_mcp_result_of_100k_reaches_the_model_as_a_preview(self, as_run) -> None:
        as_run()
        agent, client = _agent(
            [("call", "mcp_page", {}), ("call", "fetch_small", {}), ("text", "Done.")],
            tools=[_mcp_tool(), fetch_small, read_tool_result])
        await _view(agent, "on").run("go")
        seen = [t for req in client.requests[1:] for t in _tool_texts(req)]
        big = [t for t in seen if "Stored result" in t]
        assert big and all(len(t) <= PREVIEW + 300 for t in big)
        assert re.search(r"Stored result tr_[0-9a-f]{8}: 100,000 chars", big[0])
        assert "read_tool_result(ref=" in big[0]

    async def test_each_later_call_is_at_least_24k_smaller(self, as_run) -> None:
        as_run()
        script = [("call", "fetch_rows", {}), ("call", "fetch_small", {}), ("text", "Done.")]
        plain, plain_client = _agent(list(script))
        await plain.run("go")
        agent, client = _agent(list(script))
        await _view(agent, "on").run("go")
        before, after = _sizes(plain_client), _sizes(client)
        assert len(before) == len(after) == 3
        assert before[0] == after[0]
        for i in (1, 2):
            assert before[i] - after[i] >= 24000

    async def test_a_short_result_is_not_touched(self, as_run) -> None:
        as_run()
        agent, client = _agent([("call", "fetch_small", {}), ("text", "Done.")])
        await _view(agent, "on").run("go")
        assert _tool_texts(client.requests[1]) == ["three rows"]

    def test_the_injection_skips_an_agent_with_no_read_tool(self, monkeypatch) -> None:
        from acb_common import get_settings
        from orchestrator import _tool_injection as ti

        monkeypatch.setattr(get_settings(), "tool_result_spill", "on")
        bare, _ = _agent([], tools=[fetch_small])
        holder, _ = _agent([], tools=[fetch_small, read_tool_result])
        agents: list[Any] = [bare, holder]
        ti._apply_tool_result_spill(agents, "x")
        assert agents[0] is bare
        assert [type(p) for p in agents[1].context_providers] == [ToolResultSpillProvider]

    def test_the_orchestrator_holds_the_read_tool_for_the_whole_run(self, monkeypatch) -> None:
        names = _orchestrator_tools(monkeypatch, "on")
        assert "read_tool_result" in names
        assert names.index("read_tool_result") < names.index("search_timeline")

    def test_the_spill_provider_is_attached_before_the_guards(self) -> None:
        src = (ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py").read_text(
            encoding="utf-8")
        spill = src.index("    _apply_tool_result_spill(agents, agent_name)\n")
        assert spill < src.index("_apply_no_egress(agents, agent_name, injected=_egress)")
        assert spill < src.index("    _apply_answer_check(agents, agent_name)\n")


def _orchestrator_tools(monkeypatch, mode: str) -> list[str]:
    import orchestrator.agents as pkg
    from acb_common import get_settings

    mod = pkg._mod

    class _Fake:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(get_settings(), "tool_result_spill", mode)
    monkeypatch.setattr(mod, "Agent", _Fake)
    monkeypatch.setattr(mod, "_make_openai_client", lambda *a, **k: object())
    monkeypatch.setattr(mod, "_load_specialist_agents_as_tools", lambda: [])
    agent = mod.build_orchestrator_agent(with_history=False)
    return [getattr(t, "__name__", "") for t in agent.kwargs["tools"]]


# ── 4. the read tool ─────────────────────────────────────────────────────────

class TestTheReadTool:
    def _spill(self, text: str | None = None, **kw: Any) -> tuple[str, str]:
        text = text if text is not None else _big()
        owner = trs._owner_key()
        assert owner is not None
        ref = trs._keep(owner, text, kw.get("untrusted", False), "fetch_rows")
        return ref, text

    async def test_it_returns_the_next_page(self, as_run) -> None:
        as_run()
        ref, text = self._spill()
        page = await read_tool_result(ref, 3980)
        assert page.startswith(text[3980:3980 + 200])
        body, footer = page.rsplit("\n", 1)
        assert body == text[3980:3980 + trs.PAGE_CHARS]
        assert f'offset={3980 + trs.PAGE_CHARS}' in footer
        last = await read_tool_result(ref, BIG - 10)
        assert "This is the end." in last

    async def test_another_org_member_or_thread_finds_nothing(self, as_run) -> None:
        as_run("org-a", "ann@a.test", "t-1")
        ref, _ = self._spill()
        assert (await read_tool_result(ref)).startswith("row 000000")
        for org, member, thread in [
            ("org-b", "ann@a.test", "t-1"),
            ("org-a", "bob@a.test", "t-1"),
            ("org-a", "ann@a.test", "t-2"),
        ]:
            as_run(org, member, thread)
            assert await read_tool_result(ref) == NOT_FOUND
        as_run("org-a", "ann@a.test", "t-1")
        assert (await read_tool_result(ref)).startswith("row 000000")

    async def test_a_bad_ref_and_a_run_with_no_binding_find_nothing(self, as_run) -> None:
        as_run()
        ref, _ = self._spill()
        for bad in ("", "tr_zz", "../x", ref.upper(), "tr_" + "0" * 9):
            assert await read_tool_result(bad) == NOT_FOUND
        assert "offset" in await read_tool_result(ref, -1)

    async def test_an_expired_ref_says_it_is_gone(self, as_run, monkeypatch) -> None:
        as_run()
        ref, _ = self._spill()
        real = trs.time.monotonic
        monkeypatch.setattr(trs.time, "monotonic", lambda: real() + trs.TTL_S + 1)
        assert await read_tool_result(ref) == NOT_FOUND
        assert "Call the tool again" in NOT_FOUND

    def test_the_store_keeps_at_most_max_entries(self, as_run) -> None:
        as_run()
        for _ in range(trs.MAX_ENTRIES + 5):
            self._spill("x" * 10)
        assert len(trs._STORE) == trs.MAX_ENTRIES

    def test_the_total_cap_evicts_the_least_recently_used(self, as_run, monkeypatch) -> None:
        as_run()
        monkeypatch.setattr(trs, "MAX_TOTAL_CHARS", 100)
        owner = trs._owner_key()
        first = trs._keep(owner, "a" * 40, False, "t")
        second = trs._keep(owner, "b" * 40, False, "t")
        trs._STORE.move_to_end((*owner, first))  # first is now the newest
        third = trs._keep(owner, "c" * 40, False, "t")
        refs = {k[3] for k in trs._STORE}
        assert refs == {first, third} and second not in refs
        assert trs._total_chars() <= 100

    async def test_an_entry_above_the_total_cap_passes_through_whole(
        self, as_run, monkeypatch, logged,
    ) -> None:
        as_run()
        monkeypatch.setattr(trs, "MAX_TOTAL_CHARS", 50_000)
        agent, client = _agent([("call", "fetch_rows", {}), ("text", "Done.")])
        await _view(agent, "on").run("go")
        assert len(_tool_texts(client.requests[1])[0]) == BIG
        assert trs._STORE == {}
        line = [e for e in logged if e["event"] == "tool_result.size"][0]
        assert line["oversize"] is True and line["would_spill"] is True

    async def test_no_tool_argument_names_the_org_member_or_thread(self) -> None:
        import inspect

        assert list(inspect.signature(read_tool_result).parameters) == ["ref", "offset"]


# ── 5. untrusted text ────────────────────────────────────────────────────────

def _framed(body: str) -> tuple[str, str]:
    from acb_skills.untrusted import UntrustedFrame

    frame = UntrustedFrame.new()
    return frame.wrap("EMAIL", body), frame.token


class TestUntrusted:
    def test_the_preview_closes_an_open_frame_with_its_own_end_line(self) -> None:
        framed, token = _framed(_big(60_000))
        out = preview(framed, "tr_3f9c0a1b", PREVIEW)
        head, footer = out.rsplit("\n", 1)
        assert head.splitlines()[-1] == f"<<<END EMAIL {token}>>>"
        assert footer.startswith("[Stored result tr_3f9c0a1b")

    def test_a_closed_frame_gets_no_extra_end_line(self) -> None:
        framed, token = _framed("short body")
        out = preview(framed + "\n" + _big(30_000), "tr_3f9c0a1b", PREVIEW)
        assert out.count(f"<<<END EMAIL {token}>>>") == 1

    async def test_a_page_sits_in_a_fresh_frame_and_the_session_is_marked(self, as_run) -> None:
        from acb_skills import egress

        as_run("org-m", "ann@a.test", "t-mark")
        framed, old_token = _framed(_big(60_000))
        owner = trs._owner_key()
        ref = trs._keep(owner, framed, True, "get_thread")
        page = await read_tool_result(ref, 100)
        assert ("org-m", "t-mark") in egress._MARKED_SESSIONS
        opens = re.findall(r"^<<<TOOL RESULT ([0-9a-f]{16})>>>$", page, re.M)
        closes = re.findall(r"^<<<END TOOL RESULT ([0-9a-f]{16})>>>$", page, re.M)
        assert len(opens) == len(closes) == 1 and opens == closes
        assert opens[0] != old_token
        assert page.rstrip().splitlines()[-1].startswith("[Stored result")
        again = await read_tool_result(ref, 100)
        assert re.findall(r"^<<<TOOL RESULT ([0-9a-f]{16})>>>$", again, re.M) != opens

    async def test_a_trusted_page_is_plain_and_marks_nothing(self, as_run) -> None:
        from acb_skills import egress

        as_run("org-p", "ann@a.test", "t-plain")
        ref, _ = TestTheReadTool()._spill()
        page = await read_tool_result(ref)
        assert "<<<" not in page
        assert ("org-p", "t-plain") not in egress._MARKED_SESSIONS

    async def test_the_entry_is_untrusted_for_each_of_the_four_reasons(self, monkeypatch) -> None:
        plain = FunctionTool(func=fetch_small, name="fetch_small", description="")
        mcp = _mcp_tool()

        def owned(owner: str):
            async def pack_tool() -> str:
                return "x"

            pack_tool.__cc_pack_owner__ = owner  # type: ignore[attr-defined]
            return FunctionTool(func=pack_tool, name="pack_tool", description="")

        assert await trs._untrusted(plain, "clean") is False
        assert await trs._untrusted(plain, "<<<EMAIL 0123456789abcdef>>>") is True
        assert await trs._untrusted(mcp, "clean") is True
        assert await trs._untrusted(owned("email-assistant"), "clean") is True

        async def marked() -> bool:
            return True

        monkeypatch.setattr(trs, "session_is_marked", marked)
        assert await trs._untrusted(plain, "clean") is True

        async def broken() -> bool:
            raise RuntimeError("redis")

        monkeypatch.setattr(trs, "session_is_marked", broken)
        assert await trs._untrusted(plain, "clean") is True, "a failed check counts as marked"

    async def test_an_mcp_result_is_stored_as_untrusted(self, as_run) -> None:
        as_run()
        agent, _ = _agent([("call", "mcp_page", {}), ("text", "Done.")],
                          tools=[_mcp_tool(), read_tool_result])
        await _view(agent, "on").run("go")
        assert [e.untrusted for e in trs._STORE.values()] == [True]


# ── 6. no lasting row ────────────────────────────────────────────────────────

def test_the_module_writes_no_lasting_row() -> None:
    """The module imports no store, no database and no Redis. The names are
    read from the syntax tree, so a docstring may say them."""
    import ast

    tree = ast.parse((ROOT / "packages/acb_skills/acb_skills/tool_result_spill.py").read_text(
        encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    banned = {"mirror_to_blob_store", "get_db", "get_tenant_redis", "tenant_redis",
              "psycopg", "sqlalchemy", "blob_store", "write_artifact_file"}
    assert not names & banned, names & banned
    assert not any("blob" in n or "artifact_store" in n for n in names)


# ── 7. the number check ──────────────────────────────────────────────────────

CRM_BIG = "Deals, sum_amount by status:\n- Open: INR 1,234,567\n" + _big(30_000)


async def crm_aggregate(entity: str) -> str:
    """A fake CRM aggregate above the cap."""
    return CRM_BIG


class TestTheNumberCheck:
    async def test_a_result_above_the_cap_still_feeds_the_check(self, as_run) -> None:
        as_run()
        check = AnswerCheckProvider(ON)
        agent, client = _agent(
            [("call", "crm_aggregate", {"entity": "deals"}),
             ("text", "Open deals hold INR 1,234,567.")],
            tools=[crm_aggregate, read_tool_result])
        response = await _view(agent, "on", check).run("What is open?")
        assert response.text == "Open deals hold INR 1,234,567."
        assert len(client.requests) == 2, "a true figure needs no retry"
        shown = _tool_texts(client.requests[1])[0]
        assert "Stored result" in shown and len(shown) < PREVIEW + 300
        assert "1,234,567" in shown  # inside the head, so the model could quote it

    async def test_a_figure_past_the_preview_is_still_a_source(self, as_run) -> None:
        as_run()
        late = _big(30_000) + "- Won: INR 7,654,321\n"

        async def crm_report(entity: str) -> str:
            """A fake CRM report with its figure late in the text."""
            return late

        check = AnswerCheckProvider(ON)
        agent, client = _agent(
            [("call", "crm_report", {"entity": "deals"}),
             ("text", "Won deals hold INR 7,654,321.")],
            tools=[crm_report, read_tool_result])
        response = await _view(agent, "on", check).run("What is won?")
        assert response.text == "Won deals hold INR 7,654,321."
        assert len(client.requests) == 2
        assert "7,654,321" not in _tool_texts(client.requests[1])[0]


    async def test_the_real_injection_puts_the_spill_first(self, monkeypatch) -> None:
        """Through ``_inject_agent_tools``: the spill provider comes before the
        number check, so its middleware is the outermost."""
        import acb_common.settings as settings_mod
        from orchestrator import _tool_injection as ti

        monkeypatch.setattr(settings_mod, "crm_answer_check_mode", lambda: "on")
        monkeypatch.setattr(settings_mod, "tool_result_spill_mode", lambda: "on")
        agent, _ = _agent([], tools=[crm_aggregate, read_tool_result])
        agents: list[Any] = [agent]
        ti._inject_agent_tools(
            agents, tool_scope=["ask_questions"], agent_name="crm-assistant",
            agent_config={"sharing": {"instancing": "shared"}}, no_egress=False,
        )
        kinds = [type(p) for p in agents[0].context_providers]
        assert kinds.index(ToolResultSpillProvider) < kinds.index(AnswerCheckProvider)

    async def test_with_the_spill_inside_the_check_would_lose_the_figure(self, as_run) -> None:
        """Why the order matters: a spill INSIDE the check hides the late
        figure from it, and the check rejects a true answer."""
        as_run()
        late = _big(30_000) + "- Won: INR 7,654,321\n"

        async def crm_report(entity: str) -> str:
            """A fake CRM report with its figure late in the text."""
            return late

        from orchestrator._native_run_context import agent_with_providers

        agent, client = _agent(
            [("call", "crm_report", {"entity": "deals"}),
             ("text", "Won deals hold INR 7,654,321."),
             ("text", "Won deals hold INR 7,654,321.")],
            tools=[crm_report, read_tool_result])
        view = agent_with_providers(agent, [
            AnswerCheckProvider(ON), ToolResultSpillProvider("on", SPILL, PREVIEW)])
        await view.run("What is won?")
        assert len(client.requests) == 3, "the check retried: it saw only the preview"


# ── 8. exempt tools ──────────────────────────────────────────────────────────

class TestExempt:
    async def _result(self, name: str, as_run) -> str:
        as_run()

        async def tool() -> str:
            return _big()

        tool.__name__ = name
        agent, client = _agent([("call", name, {}), ("text", "Done.")],
                               tools=[tool] if name == "read_tool_result" else [tool, read_tool_result])
        await _view(agent, "on").run("go")
        return _tool_texts(client.requests[1])[0]

    @pytest.mark.parametrize("name", [
        "narrow_and_read", "call_agent", "call_agents_parallel",
        "call_agent_background", "delegate_to_agent", "ask_questions",
        "request_confirmation", "read_tool_result",
    ])
    async def test_it_never_spills(self, name: str, as_run) -> None:
        assert len(await self._result(name, as_run)) == BIG
        assert trs._STORE == {}

    async def test_a_specialist_tool_never_spills(self, as_run) -> None:
        as_run()

        async def ask_billing() -> str:
            return _big()

        ask_billing.__delegates_to__ = "billing"  # type: ignore[attr-defined]
        agent, client = _agent([("call", "ask_billing", {}), ("text", "Done.")],
                               tools=[ask_billing, read_tool_result])
        await _view(agent, "on").run("go")
        assert len(_tool_texts(client.requests[1])[0]) == BIG

    def test_the_exempt_set_names_the_narrowing_tool(self) -> None:
        assert {"narrow_and_read", "read_tool_result"} <= trs.EXEMPT_TOOLS


# ── 9. the head ──────────────────────────────────────────────────────────────

def test_the_head_is_exact() -> None:
    text = _big()
    out = preview(text, "tr_3f9c0a1b", PREVIEW)
    assert out[0:2000] == text[0:2000]
    assert len(out) <= PREVIEW + 300
    head = out.rsplit("\n", 1)[0] + "\n"
    assert text.startswith(head)
    assert text[len(head) - 1] == "\n", "cut at a line end"


def test_the_footer_names_the_size_and_the_next_offset() -> None:
    text = _big()
    out = preview(text, "tr_3f9c0a1b", PREVIEW)
    footer = out.rsplit("\n", 1)[1]
    shown = len(out.rsplit("\n", 1)[0]) + 1
    assert footer == (
        f"[Stored result tr_3f9c0a1b: 100,000 chars, ~25,000 tokens, "
        f"{text.count(chr(10)) + 1:,} lines. Shown: chars 0-{shown:,}. Call "
        f'read_tool_result(ref="tr_3f9c0a1b", offset={shown}) to read on.]'
    )


def test_a_result_with_no_line_end_is_cut_at_the_limit() -> None:
    out = preview("x" * 50_000, "tr_3f9c0a1b", PREVIEW)
    assert out.startswith("x" * PREVIEW + "\n[Stored result")


# ── The seam ─────────────────────────────────────────────────────────────────

def test_the_middleware_is_a_function_middleware_and_has_no_other_entry() -> None:
    from agent_framework import FunctionMiddleware

    assert issubclass(SpillToolResults, FunctionMiddleware)
    assert (read_tool_result,) == trs.TOOLS


def test_the_tool_risk_is_registered_and_it_is_no_egress_tool() -> None:
    from acb_skills import tool_annotations as ta
    from acb_skills.egress import is_egress_tool

    hints = ta.TOOL_ANNOTATIONS["read_tool_result"]
    assert hints["read_only"] is True and hints["open_world"] is False
    assert read_tool_result.__tool_risk__ == hints
    assert "read_tool_result" in ta._H236_UNLISTED
    assert is_egress_tool(read_tool_result) is False
    assert "read_tool_result" not in ta.risk_summary_block()
