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
   email-assistant, crm-assistant, task-manager) ended in RUN_ERROR before
   one model request. A native agent now sends ``reasoning_effort`` only.
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
    "task-manager": "apps/agents/agent-task-manager",
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

_STEER_TEXT = "Also add a task to call the supplier."


@pytest.mark.usefixtures("_a_tenant")
def test_a_steer_during_an_own_tool_only_turn_reaches_the_model(monkeypatch) -> None:
    """A second person steers while the model works. The turn then calls ONE
    own tool (``my_tasks_accounts``, which calls nothing). The steer must
    ride that tool's result into the next model request."""
    from orchestrator import steer

    thread_id = "thread-steer-own-tool"
    steer.clear_guidance(thread_id)

    def _steer_while_working(index: int, _body: dict) -> None:
        if index == 0:
            steer.buffer_guidance(thread_id, "bob@x.io", _STEER_TEXT)

    model = ScriptedModel(
        [tool_turn("my_tasks_accounts"), text_turn("noted")],
        on_request=_steer_while_working,
    )
    events, built = drive_native(
        "task-manager", _NATIVE["task-manager"], monkeypatch, model,
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
    assert getattr(tools["my_tasks_accounts"].func, "__cc_gated__", False)
