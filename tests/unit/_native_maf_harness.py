"""Shared harness: drive a REAL native MAF agent through the REAL executor.

Used by ``test_task_apis_native_maf.py`` and ``test_native_maf_wire.py``.

The agent is the one its factory builds, and the run is
``orchestrator.executor.run_agent_stream``. Only the HTTP transport under the
agent's ``AsyncOpenAI`` is replaced, by :class:`ScriptedModel`. So the request
body a test reads is the one ``OpenAIChatCompletionClient`` would send to the
gateway's ``/v1``, built by the real client from the real ``default_options``.
A stub of the client would agree with whatever options it was handed, which
is how a ``model_params`` key reached production unseen (PR #585 review).
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_agent_module(rel_dir: str) -> ModuleType:
    """Import ``<rel_dir>/agents.py`` under a unique module name, once."""
    mod_name = "_native_maf_probe_" + rel_dir.replace("/", "_").replace("-", "_")
    cached = sys.modules.get(mod_name)
    if cached is not None:
        return cached
    path = REPO_ROOT / rel_dir / "agents.py"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def parse_frames(frames: list[str]) -> list[dict[str, Any]]:
    """Turn raw ``data: {...}`` SSE lines into event dicts."""
    events: list[dict[str, Any]] = []
    for line in frames:
        for part in line.split("\n"):
            part = part.strip()
            if part.startswith("data:"):
                body = part[len("data:"):].strip()
                if body and body != "[DONE]":
                    with contextlib.suppress(json.JSONDecodeError):
                        events.append(json.loads(body))
    return events


@pytest.fixture
def _a_tenant(tmp_path, monkeypatch):
    """A shared agent's run needs a tenant for its working dir (H-201 part 3).
    Bind one, the way a request does, and clear the run state after."""
    from acb_common import get_settings
    from acb_common.db import bind_tenant, release_tenant

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    token = bind_tenant("org-native-maf-probe")
    try:
        yield
    finally:
        release_tenant(token)
        from acb_skills.write_artifact import bind_artifact_context

        bind_artifact_context()
        executor = sys.modules.get("orchestrator.executor")
        if executor is not None:
            executor._RUN_QUEUES.clear()
            executor._pending_user_input.clear()


# ── A scripted model behind a real HTTP transport ───────────────────────────


def text_turn(text: str) -> list[dict[str, Any]]:
    """One streamed assistant turn that answers with *text*."""
    return [
        {"role": "assistant", "content": text},
        {"finish_reason": "stop"},
    ]


def tool_turn(name: str, arguments: str = "{}", call_id: str = "call_1") -> list[dict[str, Any]]:
    """One streamed assistant turn that calls tool *name*."""
    return [
        {"role": "assistant", "tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": arguments},
        }]},
        {"finish_reason": "tool_calls"},
    ]


class ScriptedModel:
    """An ``httpx.MockTransport`` handler that plays turns in order.

    Records each request body in :attr:`bodies`. *on_request* runs with the
    request index and the body BEFORE the reply, which is how a test makes
    something happen "while the model is working".
    """

    def __init__(
        self,
        turns: list[list[dict[str, Any]]],
        on_request: Callable[[int, dict[str, Any]], None] | None = None,
    ) -> None:
        self.turns = turns
        self.on_request = on_request
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        index = len(self.bodies)
        self.bodies.append(body)
        if self.on_request is not None:
            self.on_request(index, body)
        turn = self.turns[min(index, len(self.turns) - 1)]
        chunks = []
        for part in turn:
            finish = part.get("finish_reason")
            delta = {k: v for k, v in part.items() if k != "finish_reason"}
            chunks.append({
                "id": f"chatcmpl-{index}", "object": "chat.completion.chunk",
                "created": 0, "model": "probe",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            })
        data = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(
            200, content=data.encode(), headers={"content-type": "text/event-stream"},
        )


def drive_native(
    name: str,
    rel_dir: str,
    monkeypatch,
    model: ScriptedModel,
    *,
    think_mode: str | None = None,
    message: str = "hi",
    thread_id: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Run agent *name* (built from *rel_dir*) through ``run_agent_stream``.

    Returns the parsed events and the agents the run built. Only the HTTP
    transport is replaced. The static registry decides the runtime.
    *history* is the earlier turns, in the shape the browser sends them.
    """
    executor = pytest.importorskip(
        "orchestrator.executor", reason="orchestrator not installed",
    )
    routes_agent = pytest.importorskip(
        "gateway.routes.agent", reason="gateway not installed",
    )
    mod = load_agent_module(rel_dir)
    agent_dir = REPO_ROOT / rel_dir
    config = json.loads((agent_dir / "config.json").read_text(encoding="utf-8"))
    built: list[Any] = []

    def _build() -> list[Any]:
        agents = mod.build_agents()
        oc = agents[0].client.client  # the AsyncOpenAI under the MAF client
        # Keep the attribution hook; replace only the wire.
        oc._client = httpx.AsyncClient(
            transport=httpx.MockTransport(model),
            event_hooks=oc._client.event_hooks,
        )
        built.extend(agents)
        return agents

    class _Loaded:
        def __init__(self) -> None:
            self.agent_dir = agent_dir
            self.agent_name = name
            self.config = config

        def build_agents(self) -> list[Any]:
            return _build()

    class _Ctx:
        def __enter__(self) -> _Loaded:
            return _Loaded()

        def __exit__(self, *_a: Any) -> bool:
            return False

    monkeypatch.setattr(executor, "load_agent", lambda *a, **k: _Ctx())
    monkeypatch.setattr(executor, "build_integrations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(routes_agent, "_load_dynamic_agents", lambda: [])

    tid = thread_id or f"thread-{name}-{think_mode or 'none'}"
    payload: dict[str, Any] = {"message": message}
    if history:
        payload["messages"] = list(history)

    async def _collect() -> list[str]:
        return [
            line async for line in executor.run_agent_stream(
                name, payload,
                run_id=f"run-{name}-{think_mode or 'none'}",
                thread_id=tid,
                think_mode=think_mode or "auto",
            )
        ]

    return parse_frames(asyncio.run(_collect())), built
