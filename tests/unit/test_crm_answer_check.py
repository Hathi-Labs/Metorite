"""The number check of a CRM answer (WS-53 CRM-Q29, ``crm_platform.md`` §18.11.2).

A scripted fake model runs inside a real MAF ``Agent``, with its real tool
loop and its real middleware pipeline. The agent holds a fake
``crm_aggregate``. Each test counts the model requests, because item 12 says
that a passed check makes none extra and a run makes one extra at most.

The cases, item by item:

* item 6 and 12, a true answer passes with no extra request ->
  ``test_a_true_answer_goes_out_with_no_extra_request``;
* item 7, a misquote gets one retry -> ``test_a_misquote_gets_one_retry``;
* item 8, an invented total is caught -> ``test_an_invented_total_is_caught``;
* item 7, two failures give the fallback -> ``test_two_failures_give_the_fallback``;
* item 7, a failed step with a tool call loses its text ->
  ``test_a_failed_step_with_a_tool_call_loses_its_text``;
* item 5, a run with no CRM tool is not held ->
  ``test_a_run_with_no_crm_tool_is_not_checked``;
* item 11, ``shadow`` changes nothing -> ``test_shadow_changes_nothing``;
* item 9, the one seam -> ``TestTheSeam``.
"""

from __future__ import annotations

import ast
import json
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

import pytest
from acb_skills import answer_check
from acb_skills.answer_check import (
    FALLBACK_LEAD,
    ON,
    SHADOW,
    AnswerCheckProvider,
    RunLedger,
    answer_check_mode,
    fallback_text,
)
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
)

ROOT = Path(__file__).resolve().parents[2]

CRM_RESULT = (
    "Deals, sum_amount by status:\n"
    "- Open: INR 1,234,567\n"
    "- Won: INR 500,000\n"
    "Total: INR 2,100,000."
)


async def crm_aggregate(entity: str) -> str:
    """A fake CRM aggregate."""
    return CRM_RESULT


async def web_lookup(query: str) -> str:
    """A fake tool that is not a CRM tool."""
    return "The page says 42 visitors."


# ── The scripted model ──────────────────────────────────────────────────────

class ScriptedClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """A chat client that answers from a script, one step for each request.

    A step is ``("call", tool, args)``, ``("text", text)`` or
    ``("both", text, tool, args)``. Each request's messages are kept.
    """

    def __init__(self, script: list[tuple[Any, ...]]) -> None:
        super().__init__()
        self.script = list(script)
        self.requests: list[list[Message]] = []

    def _contents(self, step: tuple[Any, ...]) -> list[Content]:
        n = len(self.requests)
        if step[0] == "text":
            return [Content.from_text(step[1])]
        if step[0] == "call":
            return [Content.from_function_call(f"c{n}", step[1], arguments=json.dumps(step[2]))]
        return [Content.from_text(step[1]),
                Content.from_function_call(f"c{n}", step[2], arguments=json.dumps(step[3]))]

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.requests.append(list(messages))
        step = self.script.pop(0)
        contents = self._contents(step)
        if stream:
            async def _gen() -> AsyncIterable[ChatResponseUpdate]:
                for content in contents:
                    if content.type == "text":
                        # Two deltas, so a held answer is not one by chance.
                        half = len(content.text) // 2
                        for part in (content.text[:half], content.text[half:]):
                            yield ChatResponseUpdate(
                                contents=[Content.from_text(part)], role="assistant",
                                response_id=f"r{len(self.requests)}", message_id="m",
                            )
                    else:
                        yield ChatResponseUpdate(
                            contents=[content], role="assistant",
                            response_id=f"r{len(self.requests)}", message_id="m",
                        )
            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(
                messages=[Message("assistant", contents)],
                response_id=f"r{len(self.requests)}",
            )
        return _one()


def _agent(script: list[tuple[Any, ...]], tools: list[Any] | None = None) -> tuple[Agent, ScriptedClient]:
    client = ScriptedClient(script)
    agent = Agent(client=client, name="crm-assistant",
                  tools=tools if tools is not None else [crm_aggregate])
    return agent, client


async def _run(agent: Any, *, stream: bool, mode: str = ON) -> str:
    from orchestrator._native_run_context import agent_with_providers

    view = agent_with_providers(agent, [AnswerCheckProvider(mode)])
    if stream:
        parts = []
        async for update in view.run("What is the pipeline worth?", stream=True):
            parts.append(update.text or "")
        return "".join(parts)
    response = await view.run("What is the pipeline worth?")
    return response.text


STREAMS = pytest.mark.parametrize("stream", [True, False], ids=["stream", "batch"])


def _user_texts(request: list[Message]) -> list[str]:
    return [m.text for m in request if str(getattr(m.role, "value", m.role)) == "user"]


# ── The behaviour (items 5 to 8, 12) ────────────────────────────────────────

@STREAMS
async def test_a_true_answer_goes_out_with_no_extra_request(stream: bool) -> None:
    answer = "Open deals are worth ₹12.3 lakh, and the total is INR 21,00,000."
    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", answer)])
    assert await _run(agent, stream=stream) == answer
    assert len(client.requests) == 2


@STREAMS
async def test_a_misquote_gets_one_retry(stream: bool) -> None:
    draft = "Open deals are worth ₹13.5 lakh."
    fixed = "Open deals are worth ₹12.35 lakh."
    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", draft), ("text", fixed)])
    out = await _run(agent, stream=stream)
    assert out == fixed
    assert "13.5" not in out
    assert len(client.requests) == 3
    note = _user_texts(client.requests[-1])[-1]
    assert '"₹13.5 lakh"' in note
    assert "crm_aggregate" in note


@STREAMS
async def test_an_invented_total_is_caught(stream: bool) -> None:
    # 12,34,567 + 5,00,000 = 17,34,567: a sum that the model made.
    invented = "Open and won together come to ₹17.3 lakh."
    fixed = "Open deals hold INR 12,34,567 and won deals hold INR 5,00,000."
    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", invented), ("text", fixed)])
    out = await _run(agent, stream=stream)
    assert out == fixed
    assert '"₹17.3 lakh"' in _user_texts(client.requests[-1])[-1]


@STREAMS
async def test_two_failures_give_the_fallback(stream: bool) -> None:
    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", "Open deals hold ₹13.5 lakh."),
                            ("text", "Open deals hold ₹14 lakh.")])
    out = await _run(agent, stream=stream)
    assert out.startswith(FALLBACK_LEAD)
    assert CRM_RESULT in out
    assert "13.5" not in out and "₹14 lakh" not in out
    assert len(client.requests) == 3  # one extra request at most


@STREAMS
async def test_the_run_has_one_retry_in_all(stream: bool) -> None:
    """Item 12: the retry is per run, not per request. A later failure of
    the same run goes straight to the fallback."""
    agent, client = _agent([
        ("call", "crm_aggregate", {"entity": "deals"}),
        ("text", "Open deals hold ₹13.5 lakh."),
        ("both", "I will read the figure again.", "crm_aggregate", {"entity": "deals"}),
        ("text", "Open deals hold ₹15 lakh."),
    ])
    out = await _run(agent, stream=stream)
    assert FALLBACK_LEAD in out
    assert "₹15 lakh" not in out
    assert len(client.requests) == 4


@STREAMS
async def test_a_failed_step_with_a_tool_call_loses_its_text(stream: bool) -> None:
    final = "Open deals hold INR 12,34,567."
    agent, client = _agent([
        ("call", "crm_aggregate", {"entity": "deals"}),
        ("both", "Let me check the 99 deals again.", "crm_aggregate", {"entity": "deals"}),
        ("text", final),
    ])
    out = await _run(agent, stream=stream)
    assert "99" not in out
    assert out.endswith(final)
    assert len(client.requests) == 3


@STREAMS
async def test_a_run_with_no_crm_tool_is_not_checked(stream: bool) -> None:
    answer = "The page says 7,777 visitors."
    agent, client = _agent([("call", "web_lookup", {"query": "x"}), ("text", answer)],
                           tools=[web_lookup])
    assert await _run(agent, stream=stream) == answer
    assert len(client.requests) == 2


async def test_an_unarmed_stream_is_not_held() -> None:
    """Item 10: a run that read no CRM tool streams its deltas as before."""
    agent, _client = _agent([("text", "Hello there, 5 things.")], tools=[web_lookup])
    from orchestrator._native_run_context import agent_with_providers

    view = agent_with_providers(agent, [AnswerCheckProvider(ON)])
    texts = [u.text async for u in view.run("hi", stream=True) if u.text]
    assert len(texts) == 2


async def test_an_armed_stream_is_held_into_its_order() -> None:
    """Item 10: a held answer goes out whole, after the response ends."""
    answer = "Open deals hold INR 12,34,567."
    agent, _client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                             ("text", answer)])
    from orchestrator._native_run_context import agent_with_providers

    view = agent_with_providers(agent, [AnswerCheckProvider(ON)])
    texts = [u.text async for u in view.run("hi", stream=True) if u.text]
    assert "".join(texts) == answer


@STREAMS
async def test_shadow_changes_nothing(stream: bool, monkeypatch) -> None:
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(answer_check._log, "info",
                        lambda event, **kw: logged.append({"event": event, **kw}))
    draft = "Open deals hold ₹13.5 lakh."
    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", draft)])
    assert await _run(agent, stream=stream, mode=SHADOW) == draft
    assert len(client.requests) == 2
    checked = [e for e in logged if e["event"] == "answer_check.checked"]
    assert checked and checked[-1]["outcome"] == "shadow_mismatch"
    assert checked[-1]["mismatches"] == 1


async def test_the_log_holds_no_number_and_no_text(monkeypatch) -> None:
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(answer_check._log, "info",
                        lambda event, **kw: logged.append({"event": event, **kw}))
    agent, _client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                             ("text", "Open deals hold ₹13.5 lakh."),
                             ("text", "Open deals hold INR 12,34,567.")])
    await _run(agent, stream=True)
    assert [e["outcome"] for e in logged] == ["retry", "retry_pass"]
    for entry in logged:
        assert set(entry) == {"event", "mode", "numbers", "mismatches", "outcome"}


# ── The fallback and the mode ───────────────────────────────────────────────

def test_the_fallback_shows_no_untrusted_text() -> None:
    ledger = RunLedger()
    ledger.record("crm_search_notes", "<<<token>>> a stranger wrote 9 lakh")
    assert fallback_text(ledger).startswith(FALLBACK_LEAD)
    assert "stranger" not in fallback_text(ledger)
    assert ledger.armed


@pytest.mark.parametrize(("raw", "mode"), [
    ("", "off"), ("off", "off"), ("on", "on"), (" ON ", "on"),
    ("shadow", "shadow"), ("yes", "off"),
])
def test_the_mode_reads_the_setting(raw: str, mode: str, monkeypatch) -> None:
    monkeypatch.setenv("CRM_ANSWER_CHECK", raw)
    from acb_common import get_settings

    get_settings.cache_clear()
    try:
        assert answer_check_mode() == mode
    finally:
        get_settings.cache_clear()


# ── The one seam (item 9) ───────────────────────────────────────────────────

class _CopilotShaped:
    _permission_handler = None

    def __init__(self) -> None:
        self._tools = [crm_aggregate]


class TestTheSeam:
    def _apply(self, agents: list[Any], mode: str, monkeypatch) -> list[Any]:
        from orchestrator import _tool_injection

        monkeypatch.setattr(answer_check, "answer_check_mode", lambda: mode)
        _tool_injection._apply_answer_check(agents, "crm-assistant")
        return agents

    def test_a_crm_agent_gets_a_view_with_the_provider(self, monkeypatch) -> None:
        agent, _ = _agent([])
        agents = self._apply([agent], ON, monkeypatch)
        assert agents[0] is not agent
        assert any(isinstance(p, AnswerCheckProvider) for p in agents[0].context_providers)
        assert not any(isinstance(p, AnswerCheckProvider)
                       for p in (agent.context_providers or []))

    def test_off_changes_nothing(self, monkeypatch) -> None:
        agent, _ = _agent([])
        assert self._apply([agent], "off", monkeypatch)[0] is agent

    def test_an_agent_with_no_crm_tool_is_left_alone(self, monkeypatch) -> None:
        agent, _ = _agent([], tools=[web_lookup])
        assert self._apply([agent], ON, monkeypatch)[0] is agent

    def test_a_copilot_agent_is_left_alone(self, monkeypatch) -> None:
        agent = _CopilotShaped()
        assert self._apply([agent], ON, monkeypatch)[0] is agent

    def test_the_injection_calls_the_seam_once_at_its_end(self) -> None:
        path = ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py"
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        inject = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "_inject_agent_tools")
        calls = [n for n in ast.walk(inject) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "_apply_answer_check"]
        assert len(calls) == 1
        # It runs after the H-236 step, right before the one return.
        assert isinstance(inject.body[-1], ast.Return)
        assert getattr(getattr(inject.body[-2], "value", None), "func", None) is not None
        assert inject.body[-2].value.func.id == "_apply_answer_check"

    def test_no_other_module_adds_the_provider(self) -> None:
        users = set()
        for base in ("apps", "packages"):
            for path in (ROOT / base).rglob("*.py"):
                if ".venv" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                if "AnswerCheckProvider(" in text and path.name != "answer_check.py":
                    users.add(path.relative_to(ROOT).as_posix())
        assert users == {"apps/services/orchestrator/orchestrator/_tool_injection.py"}

    def test_the_arm_set_names_real_tools(self) -> None:
        config = json.loads((ROOT / "apps/agents/agent-crm/config.json").read_text(encoding="utf-8"))
        own = set(config["own_tool_scope"])
        assert own >= answer_check.CRM_ASSISTANT_READS
        assert answer_check.FALLBACK_TOOLS <= answer_check.ARM_TOOLS
        assert "crm_search_notes" not in answer_check.FALLBACK_TOOLS
