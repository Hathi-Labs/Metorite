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
* item 12, one retry for each run -> ``test_the_run_has_one_retry_in_all``;
* item 4, the model's text is never a source ->
  ``test_the_model_s_own_text_is_never_a_source``;
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


async def crm_aggregate(
    entity: str, updated_from: str | None = None, page_size: int | None = None,
) -> str:
    """A fake CRM aggregate."""
    return CRM_RESULT


#: Each card that the fake ``emit_generative_ui`` drew, in order.
CARDS: list[str] = []


async def emit_generative_ui(ui: str) -> str:
    """A fake card tool. It keeps each card it draws."""
    CARDS.append(ui)
    return "drawn"


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
                  tools=tools if tools is not None else [crm_aggregate, emit_generative_ui])
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
    # No pair of tool figures gives 18,90,000: the model made it up.
    invented = "Open and won together come to ₹18.9 lakh."
    fixed = "Open deals hold INR 12,34,567 and won deals hold INR 5,00,000."
    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", invented), ("text", fixed)])
    out = await _run(agent, stream=stream)
    assert out == fixed
    assert '"₹18.9 lakh"' in _user_texts(client.requests[-1])[-1]


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
async def test_the_model_s_own_text_is_never_a_source(stream: bool) -> None:
    """Item 4: an earlier answer of the model holds an invented number. When
    the model says it again, the check still catches it."""
    from orchestrator._native_run_context import agent_with_providers

    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", "You still have ₹99 lakh open."),
                            ("text", "You have INR 12,34,567 open.")])
    view = agent_with_providers(agent, [AnswerCheckProvider(ON)])
    history = [Message("user", ["What is open?"]),
               Message("assistant", ["You had ₹99 lakh open."]),
               Message("user", ["And now?"])]
    if stream:
        out = "".join([u.text or "" async for u in view.run(history, stream=True)])
    else:
        out = (await view.run(history)).text
    assert out == "You have INR 12,34,567 open."
    assert len(client.requests) == 3


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
        import acb_common.settings as settings_mod
        from orchestrator import _tool_injection

        monkeypatch.setattr(settings_mod, "crm_answer_check_mode", lambda: mode)
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


# ── Fix round (2026-10-17): the review of Batch I2 ─────────────────────────

def _card(value: int) -> dict[str, Any]:
    return {"ui": json.dumps({"template": "kpi", "width": 3,
                              "items": [{"label": "Open", "value": value}]})}


@pytest.fixture(autouse=True)
def _no_cards() -> Any:
    CARDS.clear()
    yield
    CARDS.clear()


@STREAMS
async def test_a_bad_card_with_clean_text_is_never_shipped(stream: bool) -> None:
    """Item 7, fix 2: the card shows its arguments, so a made-up total in a
    card fails the answer. The bad card is never drawn, and the retry is."""
    agent, client = _agent([
        ("call", "crm_aggregate", {"entity": "deals"}),
        ("both", "Here is the pipeline.", "emit_generative_ui", _card(1834567)),
        ("both", "Here is the pipeline.", "emit_generative_ui", _card(1234567)),
        ("text", "Done."),
    ])
    out = await _run(agent, stream=stream)
    assert [_card(1234567)["ui"]] == CARDS
    assert out.endswith("Done.")
    assert '"1834567"' in _user_texts(client.requests[2])[-1]
    assert len(client.requests) == 4


@STREAMS
async def test_a_bad_card_with_bad_text_gives_the_fallback(stream: bool) -> None:
    agent, client = _agent([
        ("call", "crm_aggregate", {"entity": "deals"}),
        ("both", "Open and won hold ₹18.3 lakh.", "emit_generative_ui", _card(1834567)),
        ("both", "Still ₹18.3 lakh.", "emit_generative_ui", _card(1834567)),
    ])
    out = await _run(agent, stream=stream)
    assert CARDS == []
    assert out.startswith(FALLBACK_LEAD)
    assert len(client.requests) == 3


def test_the_shown_text_of_a_card_skips_layout_and_code() -> None:
    from acb_skills.answer_check import display_text

    ui = json.dumps({"template": "kpi", "width": 7, "color": "#123456",
                     "items": [{"label": "Won 12 deals", "value": 500000}]})
    shown = display_text("emit_generative_ui", {"ui": ui})
    assert "500000" in shown and "Won 12 deals" in shown
    assert "7" not in shown.split() and "123456" not in shown
    html = "<div style='width:300px'>Open <b>INR 12,34,567</b></div>"
    assert "300" not in display_text("emit_generative_ui", {"ui": html})
    assert "12,34,567" in display_text("emit_generative_ui", {"ui": html})
    assert display_text("write_artifact", {"path": "outputs/a.py", "content": "x = 99"}) == ""
    assert "99" in display_text("write_artifact", {"path": "outputs/a.md", "content": "99 deals"})
    assert display_text("crm_aggregate", {"entity": "deals"}) == ""


@STREAMS
async def test_the_arguments_of_a_crm_call_are_sources(stream: bool) -> None:
    """Fix 4: "the last 7 days" after updated_from, "top 5" after page_size."""
    from datetime import date, timedelta

    since = (date.today() - timedelta(days=7)).isoformat()
    answer = "In the last 7 days, the top 5 stages hold INR 12,34,567."
    agent, client = _agent([
        ("call", "crm_aggregate", {"entity": "deals", "updated_from": since, "page_size": 5}),
        ("text", answer),
    ])
    assert await _run(agent, stream=stream) == answer
    assert len(client.requests) == 2


@STREAMS
async def test_an_invented_number_still_fails_beside_the_arguments(stream: bool) -> None:
    from datetime import date, timedelta

    since = (date.today() - timedelta(days=7)).isoformat()
    agent, client = _agent([
        ("call", "crm_aggregate", {"entity": "deals", "updated_from": since, "page_size": 5}),
        ("text", "In the last 9 days, 6 stages moved."),
        ("text", "In the last 7 days, 5 stages moved."),
    ])
    assert await _run(agent, stream=stream) == "In the last 7 days, 5 stages moved."
    assert len(client.requests) == 3


@STREAMS
async def test_an_approximation_and_a_phone_pass(stream: bool) -> None:
    answer = "Won deals hold about ₹4.7 lakh. Call the owner on +91 98450 12345."
    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", answer)])
    assert await _run(agent, stream=stream) == answer
    assert len(client.requests) == 2


def test_a_delegated_answer_is_no_source() -> None:
    """Fix 1: the answer of another agent is text that a model wrote."""
    ledger = RunLedger()
    ledger.record("crm_aggregate", CRM_RESULT)
    ledger.record("call_agent", "The total is ₹99 lakh.", delegated=True)
    assert not ledger.check("₹99 lakh").ok
    assert ledger.check("INR 12,34,567").ok


def test_the_member_s_words_come_from_the_run_context() -> None:
    from acb_skills.answer_check import MEMBER_MESSAGE, member_words
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context

    payload = {"message": "Show the deals over ₹7 lakh"}
    assert member_words({}, payload) == "Show the deals over ₹7 lakh"
    # A delegated run takes the parent's words, never its own payload.
    parent = {"run_id": "r1", MEMBER_MESSAGE: "What is open?"}
    assert member_words(parent, {"message": "the total is ₹99 lakh"}) == "What is open?"
    ledger = RunLedger()
    ledger.record("crm_aggregate", CRM_RESULT)
    with artifact_context_scope():
        bind_artifact_context(**{MEMBER_MESSAGE: payload["message"]})
        assert ledger.check("The deals over ₹7 lakh hold INR 12,34,567.").ok
    assert not ledger.check("The deals over ₹7 lakh hold INR 12,34,567.").ok


@STREAMS
async def test_the_packed_prompt_of_production_is_no_source(stream: bool) -> None:
    """Fix 1: with MAF_NATIVE_SESSIONS off, the executor packs the history,
    the model's earlier answers too, into ONE user string. A number the model
    invented in turn 1 must not become a source in turn 2."""
    from acb_skills.answer_check import member_words
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from orchestrator import executor
    from orchestrator._native_run_context import agent_with_providers

    payload = {
        "message": "And now?",
        "messages": [{"role": "user", "content": "What is open?"},
                     {"role": "assistant", "content": "You had ₹99 lakh open."}],
    }
    packed = executor._compose_maf_run_input("crm-assistant", "r1", payload, {})
    assert "₹99 lakh" in packed  # the shape that production sends
    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", "You still have ₹99 lakh open."),
                            ("text", "You have INR 12,34,567 open.")])
    view = agent_with_providers(agent, [AnswerCheckProvider(ON)])
    with artifact_context_scope():
        bind_artifact_context(member_message=member_words({}, payload))
        if stream:
            out = "".join([u.text or "" async for u in view.run(packed, stream=True)])
        else:
            out = (await view.run(packed)).text
    assert out == "You have INR 12,34,567 open."
    assert len(client.requests) == 3


async def test_a_hold_sends_a_sign_of_life_for_each_update(monkeypatch) -> None:
    """Fix 3: while it holds, the check beats on each update of the model."""
    beats: list[int] = []
    monkeypatch.setattr(answer_check, "HEARTBEAT_S", 0.0)
    monkeypatch.setattr(answer_check, "_beat", lambda: beats.append(1))
    agent, _client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                             ("text", "Open deals hold INR 12,34,567.")])
    await _run(agent, stream=True)
    assert len(beats) >= 2  # the held answer streams two deltas


def test_the_executor_knows_the_hold_event() -> None:
    from orchestrator import executor

    assert executor._is_hold_beat({"type": "CUSTOM", "name": answer_check.HOLD_EVENT})
    assert not executor._is_hold_beat({"type": "CUSTOM", "name": "artifact_created"})


@pytest.mark.parametrize("mode", ["off", "on"])
def test_a_box_with_no_check_module_still_runs(mode: str, monkeypatch) -> None:
    """Fix 6: the mode is read before the import, and a missing module
    never raises."""
    import sys

    import acb_common.settings as settings_mod
    from orchestrator import _tool_injection

    monkeypatch.setattr(settings_mod, "crm_answer_check_mode", lambda: mode)
    monkeypatch.setitem(sys.modules, "acb_skills.answer_check", None)
    agent, _ = _agent([])
    agents = [agent]
    _tool_injection._apply_answer_check(agents, "crm-assistant")
    assert agents[0] is agent


@STREAMS
async def test_a_difference_of_two_tool_figures_passes(stream: bool) -> None:
    """Fix round 2, A: a founder asks for open minus won. The answer states
    the difference of two tool figures, with no extra request."""
    answer = "Open deals exceed won deals by ₹7.3 lakh."
    agent, client = _agent([("call", "crm_aggregate", {"entity": "deals"}),
                            ("text", answer)])
    assert await _run(agent, stream=stream) == answer
    assert len(client.requests) == 2


@pytest.mark.parametrize("mode", ["off", "shadow", "on"])
def test_the_injection_puts_the_provider_on_the_agent(mode: str, monkeypatch) -> None:
    """Fix round 2, C: the real ``_inject_agent_tools`` path, not a source
    scan. The provider is on the agent for shadow and on, and absent for
    off."""
    import acb_common.settings as settings_mod
    from orchestrator import _tool_injection

    monkeypatch.setattr(settings_mod, "crm_answer_check_mode", lambda: mode)
    agent, _ = _agent([])
    agents = [agent]
    _tool_injection._inject_agent_tools(
        agents, tool_scope=["ask_questions"], agent_name="crm-assistant",
        agent_config={"sharing": {"instancing": "shared"}}, no_egress=False,
    )
    providers = [p for p in (agents[0].context_providers or [])
                 if isinstance(p, AnswerCheckProvider)]
    if mode == "off":
        assert providers == []
    else:
        assert len(providers) == 1
        assert providers[0].mode == mode
