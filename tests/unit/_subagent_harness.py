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


# ── AUTO-8b-2c: the headers of a worker run, through the REAL run_agent ─────


class HeaderWire:
    """An async ``httpx.MockTransport`` handler for the attribution fences.

    It answers each request with one text completion, and keeps the headers
    of each request, in lower case, in :attr:`headers`. When :attr:`gate` is
    set, each request awaits it before the answer, so two runs can meet on
    the wire.
    """

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []
        self.gate: Callable[[], Any] | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.headers.append({k.lower(): v for k, v in request.headers.items()})
        if self.gate is not None:
            await self.gate()
        return httpx.Response(200, json=completion(text='{"items": []}'))

    def of_run(self, run_id: str) -> list[dict[str, str]]:
        """The headers of each request that names the run *run_id*."""
        return [h for h in self.headers if h.get("x-cc-run") == run_id]


class _WorkerLoad:
    """What ``executor.load_agent`` gives back: the REAL worker on *wire*.

    The executor calls ``build_agents`` once for each run, on the frame of
    that run, as the loader does. Only the transport under the client's own
    request hook (the attribution stamp) is replaced.
    """

    def __init__(self, wire: HeaderWire, agent_dir: Path) -> None:
        self.agent_dir = agent_dir
        self.agent_name = "worker"
        self.config = json.loads((REPO_ROOT / WORKER_DIR / "config.json").read_text(
            encoding="utf-8"))
        self._wire = wire

    def build_agents(self) -> list[Any]:
        agent = load_agent_module(WORKER_DIR).build_agents()[0]
        oc = agent.client.client  # the AsyncOpenAI under the MAF client
        oc._client = httpx.AsyncClient(
            transport=httpx.MockTransport(self._wire), event_hooks=oc._client.event_hooks,
        )
        return [agent]

    def cleanup(self) -> None:
        return None

    def __enter__(self) -> _WorkerLoad:
        return self

    def __exit__(self, *_a: Any) -> bool:
        return False


def route_worker_runs(monkeypatch: Any, tmp_path: Path, wire: HeaderWire) -> None:
    """Make each ``executor.run_agent`` load the REAL worker on *wire*.

    No clone, no credential and no audit row. The working dirs land in
    *tmp_path*. A caller passes ``organization_id``, so the run binds its own
    tenant.
    """
    from acb_common import get_settings
    from orchestrator import executor

    agent_dir = tmp_path / "worker-clone"
    agent_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _WorkerLoad(wire, agent_dir))
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(executor, "record", lambda *a, **k: None)
