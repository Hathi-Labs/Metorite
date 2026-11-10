"""Shared harness for the OA-9 fences (WS-67): drive the REAL worker agent.

Used by ``test_subagent_worker.py``, ``test_spawn_subagents.py`` and
``test_subagent_blocked_tools.py``.

The agent is the one ``apps/agents/agent-worker/agents.py`` builds, with the
job bound on the frame as ``spawn_subagents`` binds it, and with the tools
added to its pool as ``_inject_agent_tools`` adds them. Only the HTTP wire
under the agent's ``AsyncOpenAI`` is replaced, by :class:`WireModel`. So the
request body a test reads is the one the real client would send to the
gateway's ``/v1``, and the middleware pair is the real one. A stub of the
client would agree with whatever it was handed (R8 spirit).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from tests.unit._native_maf_harness import load_agent_module

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_DIR = "apps/agents/agent-worker"


def completion(text: str | None = None, calls: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """One non-streamed chat completion: *text*, or tool calls ``(name, arguments)``."""
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": name, "arguments": args}}
            for i, (name, args) in enumerate(calls)
        ]
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "probe",
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class WireModel:
    """An ``httpx.MockTransport`` handler that plays completions in order.

    It records each request body in :attr:`bodies`. The last reply repeats, so
    a script of one tool call makes a child that calls a tool for ever.
    """

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = replies
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        index = len(self.bodies)
        self.bodies.append(body)
        reply = self.replies[min(index, len(self.replies) - 1)]
        return httpx.Response(200, json=reply)

    def tool_names(self, index: int) -> list[str]:
        """The names of the tools in request *index*."""
        return [t["function"]["name"] for t in self.bodies[index].get("tools") or []]


def build_worker(wire: WireModel, tools: list[Any] | None = None) -> Any:
    """The real worker agent for the job on this frame, on the scripted wire.

    *tools* join the pool of the agent, as the executor's injection adds them.
    """
    mod = load_agent_module(WORKER_DIR)
    agent = mod.build_agents()[0]
    oc = agent.client.client  # the AsyncOpenAI under the MAF client
    oc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(wire), event_hooks=oc._client.event_hooks,
    )
    if tools:
        agent.default_options["tools"].extend(tools)
    return agent


def counted(name: str, calls: list[str], *, result: str = "ran") -> Callable[..., Any]:
    """A tool called *name* whose body appends to *calls* and returns *result*.

    The body takes no argument, so a model call with ``{}`` runs it. The count
    of ``calls`` is the count of runs of the BODY.
    """

    async def _tool() -> str:
        calls.append(name)
        return result

    _tool.__name__ = name
    _tool.__doc__ = f"The test tool {name}."
    return _tool
