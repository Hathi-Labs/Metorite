"""What a native MAF agent puts on the wire, through the REAL executor.

Spec: ``project-docs/specs/agent_architecture.md`` §11.3.1. PR #585 review.

Two live bugs hid behind fakes that agreed with whatever they were handed.
Each test here runs a real factory agent through ``run_agent_stream`` with a
real ``OpenAIChatCompletionClient``. Only the HTTP transport under it is a
script (:class:`tests.unit._native_maf_harness.ScriptedModel`).

1. **Think mode.** ``_apply_thinking_mode`` wrote ``model_params`` and
   ``thinking`` into a native agent's ``default_options``. The client passes
   every key to ``AsyncCompletions.create()``, which refused both. So every
   Thinking or Max turn of every native agent (projects-assistant,
   email-assistant, crm-assistant, whatsapp-assistant, apis-config) ended in
   RUN_ERROR before one model request. A native agent now sends
   ``reasoning_effort`` only.
2. **Steer.** On Tier 1 a native agent's OWN tools had no
   ``_gate_injected_tool`` wrapper, and that wrapper is the only steer drain.
   A steer that arrived during a turn that called only own tools was lost.
   Own tools are gated now, and the schema the model sees did not change.
"""
from __future__ import annotations

import pytest

from tests.unit._native_maf_harness import (
    ScriptedModel,
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    load_agent_module,
    text_turn,
    tool_turn,
)

pytest.importorskip("agent_framework", reason="agent_framework not installed")
openai_params = pytest.importorskip(
    "openai.types.chat.completion_create_params", reason="openai not installed",
)

#: Every key ``AsyncCompletions.create()`` accepts in a request body.
_VALID_KEYS = frozenset(openai_params.CompletionCreateParamsBase.__annotations__) | {
    "stream",
}

#: Registry name → agent directory, for the native agents these tests drive.
_NATIVE = {
    "apis-config": "apps/agents/agent-apis-config",
    "projects-assistant": "apps/agents/agent-projects",
}

#: Think mode → the ``reasoning_effort`` the request must carry (None: absent).
_EXPECTED_EFFORT = {"auto": None, "thinking": "medium", "max": "high"}


def _own_tool_specs(rel_dir: str) -> dict[str, dict]:
    """The tool schemas a freshly built agent offers, before any injection."""
    agent = load_agent_module(rel_dir).build_agents()[0]
    return {
        t.name: t.to_json_schema_spec()
        for t in agent.default_options["tools"]
        if hasattr(t, "to_json_schema_spec")
    }


# ── 1. Think mode ────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("_a_tenant")
@pytest.mark.parametrize("think_mode", sorted(_EXPECTED_EFFORT))
@pytest.mark.parametrize("name", sorted(_NATIVE))
def test_every_think_mode_reaches_the_model_and_finishes(
    name: str, think_mode: str, monkeypatch,
) -> None:
    model = ScriptedModel([text_turn("done")])
    events, _ = drive_native(name, _NATIVE[name], monkeypatch, model, think_mode=think_mode)
    types = [e.get("type") for e in events]

    errors = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert not errors, errors
    assert types[-1] == "RUN_FINISHED"
    assert model.bodies, "no request reached the model"
    for body in model.bodies:
        unknown = set(body) - _VALID_KEYS
        assert not unknown, f"keys the Chat Completions API does not take: {unknown}"
        assert "model_params" not in body and "thinking" not in body
        assert body.get("reasoning_effort") == _EXPECTED_EFFORT[think_mode]


@pytest.mark.usefixtures("_a_tenant")
@pytest.mark.parametrize("name", sorted(_NATIVE))
def test_the_schema_the_model_sees_is_the_factorys(name: str, monkeypatch) -> None:
    """Gating an own tool swaps its ``func`` only. The name, the description
    and the parameter schema on the wire stay the ones the factory built."""
    expected = _own_tool_specs(_NATIVE[name])
    model = ScriptedModel([text_turn("done")])
    drive_native(name, _NATIVE[name], monkeypatch, model)

    sent = {t["function"]["name"]: t for t in model.bodies[0]["tools"]}
    assert set(expected) <= set(sent)
    for tool_name, spec in expected.items():
        assert sent[tool_name] == spec, f"{tool_name} schema changed on the wire"


# ── 2. Steer ─────────────────────────────────────────────────────────────────

_STEER_TEXT = "Also cover the Drive API, please."


@pytest.mark.usefixtures("_a_tenant")
def test_a_steer_during_an_own_tool_only_turn_reaches_the_model(monkeypatch) -> None:
    """A second person steers while the model works. The turn then calls ONE
    own tool (apis-config's ``web_search``, offline here). The steer must
    ride that tool's result into the next model request."""
    import acb_skills.web_tools as web_tools
    from orchestrator import steer

    async def _fake_serp(query: str, max_results: int) -> list[dict]:
        return [{"title": "Sheets API", "href": "https://example.test/sheets", "body": "x"}]

    monkeypatch.setattr(web_tools, "_serpapi_search", _fake_serp)

    thread_id = "thread-steer-own-tool"
    steer.clear_guidance(thread_id)

    def _steer_while_working(index: int, _body: dict) -> None:
        if index == 0:
            steer.buffer_guidance(thread_id, "bob@x.io", _STEER_TEXT)

    model = ScriptedModel(
        [tool_turn("web_search", '{"query": "Google Sheets API"}'), text_turn("noted")],
        on_request=_steer_while_working,
    )
    events, built = drive_native(
        "apis-config", _NATIVE["apis-config"], monkeypatch, model,
        thread_id=thread_id,
    )

    assert not [e for e in events if e.get("type") == "RUN_ERROR"], events
    assert [e.get("type") for e in events][-1] == "RUN_FINISHED"
    assert len(model.bodies) == 2, "the tool result never went back to the model"
    tool_messages = [m for m in model.bodies[1]["messages"] if m.get("role") == "tool"]
    assert tool_messages, model.bodies[1]["messages"]
    assert any(_STEER_TEXT in str(m.get("content")) for m in tool_messages), (
        "the steer was buffered but never reached the model"
    )
    assert not steer.has_guidance(thread_id)

    # The own tool the model called is the gated copy, not the bare function.
    tools = {t.name: t for t in built[0].default_options["tools"] if hasattr(t, "name")}
    assert getattr(tools["web_search"].func, "__cc_gated__", False)


# ── 3. The gate never mutates a tool that agents share ───────────────────────


async def _shared_probe_tool(text: str, count: int = 1) -> str:
    """Echo the text.

    Args:
        text: The text to echo.
        count: How many times.
    """
    return text * count


def test_a_shared_module_level_tool_is_never_mutated(monkeypatch) -> None:
    """R7 fence for the copy in ``_gate_own_maf_tools``.

    A factory can hand the SAME module-level ``FunctionTool`` to every agent
    it builds, and one process serves many runs. If the gate wrapped that
    object in place, the next agent would hold a tool that is already
    wrapped, and the shared tool would change for every caller in the
    process. Each agent must get its own gated copy. The shared object keeps
    its original function and its schema.
    """
    import json

    import openai
    from agent_framework import Agent, FunctionTool
    from agent_framework.openai import OpenAIChatCompletionClient
    from orchestrator._tool_injection import _inject_agent_tools

    monkeypatch.delenv("AGENT_PERMISSION_MODE", raising=False)  # enforce
    shared = FunctionTool(
        func=_shared_probe_tool, name="shared_probe_tool", description="Echo the text.",
    )
    original_func = shared.func
    spec_before = json.dumps(shared.to_json_schema_spec(), sort_keys=True)

    def _agent(name: str) -> Agent:
        client = OpenAIChatCompletionClient(
            model="tier-balanced",
            async_client=openai.AsyncOpenAI(base_url="http://127.0.0.1:9/v1", api_key="x"),
        )
        return Agent(client=client, instructions="x", name=name, tools=[shared])

    first, second = _agent("probe-one"), _agent("probe-two")
    # The precondition that makes this test mean something: each agent holds
    # the very object the module holds.
    assert first.default_options["tools"][0] is shared
    assert second.default_options["tools"][0] is shared

    _inject_agent_tools([first], agent_name="probe-one", tool_scope=["ask_questions"])
    _inject_agent_tools([second], agent_name="probe-two", tool_scope=["ask_questions"])

    assert shared.func is original_func, "the gate rewrote the shared tool in place"
    assert not getattr(shared.func, "__cc_gated__", False)
    assert json.dumps(shared.to_json_schema_spec(), sort_keys=True) == spec_before

    held = []
    for agent in (first, second):
        [tool] = [
            t for t in agent.default_options["tools"]
            if getattr(t, "name", None) == "shared_probe_tool"
        ]
        assert tool is not shared, "the agent holds the shared object, not a copy"
        assert getattr(tool.func, "__cc_gated__", False), "the agent's copy is not gated"
        assert tool.func.__wrapped__ is original_func, "the copy wraps a wrapper"
        assert json.dumps(tool.to_json_schema_spec(), sort_keys=True) == spec_before
        held.append(tool)
    assert held[0] is not held[1], "the two agents share one gated copy"
