"""Golden trajectories: the structured history path of a native MAF run (WS-43t1).

Locks ``project-docs/specs/maf_coding_engine.md`` §15.9 for what the MODEL
receives on a two-turn chat with the member's memory, behind
``MAF_NATIVE_SESSIONS``:

  1. Flag OFF (the default): one user message holds the whole prompt, with the
     memory and the earlier turns inside it as text. Today's trajectory.
  2. Flag ON: the earlier turns arrive as turns. The memory rides in the system
     message, after the agent's own instructions and the prompt-cache
     sentinel, never in a user message. The gateway's caching transform then
     strips the sentinel for a non-Anthropic model, so the model never sees it.

A real ``agent_framework.Agent`` and the real OpenAI chat client build the
request. Only the HTTP transport is a script. The unit fence is
``tests/unit/test_native_session_persistence.py`` (WS43-F20).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

agent_framework = pytest.importorskip("agent_framework", reason="agent_framework not installed")

from acb_llm.prompt_cache import CACHE_BREAK, apply_prompt_caching  # noqa: E402
from orchestrator import executor  # noqa: E402

_INSTRUCTIONS = "You are the inbox assistant."
_MEMORY = "The member prefers short answers."
_PAYLOAD: dict[str, Any] = {
    "message": "yes",
    "messages": [
        {"role": "user", "content": "Process my inbox."},
        {"role": "assistant", "content": "Apply itm-1 and itm-2?"},
    ],
    "memory_context": _MEMORY,
}


def _model_request(monkeypatch, *, flag: bool) -> list[dict[str, Any]]:
    """The ``messages`` a real native run sends to the model for _PAYLOAD."""
    import openai
    from acb_common import get_settings
    from agent_framework.openai import OpenAIChatCompletionClient

    monkeypatch.setattr(get_settings(), "maf_native_sessions", flag)
    bodies: list[dict[str, Any]] = []

    def _reply(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content or b"{}"))
        chunk = {
            "id": "c", "object": "chat.completion.chunk", "created": 0, "model": "p",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Done."},
                         "finish_reason": "stop"}],
        }
        return httpx.Response(
            200, content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode(),
            headers={"content-type": "text/event-stream"},
        )

    agent = agent_framework.Agent(
        client=OpenAIChatCompletionClient(
            model="tier-balanced",
            async_client=openai.AsyncOpenAI(
                base_url="http://127.0.0.1:9/v1", api_key="x",
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(_reply)),
            ),
        ),
        instructions=_INSTRUCTIONS,
        name="trajectory-probe",
    )
    run_input, provider = executor._compose_maf_run(
        "trajectory-probe", "run-t", dict(_PAYLOAD), {}, native=True,
    )

    async def _run() -> None:
        stream = executor._agent_for_run(agent, provider).run(run_input, stream=True)
        async for _update in stream:
            pass

    asyncio.run(_run())
    assert agent.context_providers == []
    return bodies[0]["messages"]


def test_flag_off_the_model_reads_one_prompt_string(monkeypatch):
    messages = _model_request(monkeypatch, flag=False)
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == _INSTRUCTIONS
    prompt = messages[1]["content"]
    assert _MEMORY in prompt
    assert "Conversation history:\nUser: Process my inbox.\nAssistant: Apply itm-1 and itm-2?" in prompt
    assert prompt.endswith("\nyes")


def test_flag_on_the_model_reads_turns_and_memory_in_the_system_message(monkeypatch):
    messages = _model_request(monkeypatch, flag=True)
    assert [(m["role"], m["content"]) for m in messages[1:]] == [
        ("user", "Process my inbox."),
        ("assistant", "Apply itm-1 and itm-2?"),
        ("user", "yes"),
    ]
    system = messages[0]["content"]
    assert system == (
        f"{_INSTRUCTIONS}\n{CACHE_BREAK}\n## Memory from past conversations\n{_MEMORY}"
    )
    # The gateway's one caching transform strips the sentinel for a
    # non-Anthropic model, so the model reads the memory and never the marker.
    wired, _tools, _extra = apply_prompt_caching(
        model="deepseek/deepseek-chat", messages=messages,
    )
    assert CACHE_BREAK not in wired[0]["content"]
    assert wired[0]["content"].startswith(_INSTRUCTIONS)
    assert _MEMORY in wired[0]["content"]
