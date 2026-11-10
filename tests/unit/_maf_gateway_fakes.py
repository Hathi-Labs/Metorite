"""A fake gateway ``/v1`` for the WS-43e suites (WS43-F7, F8, F13).

⚠️ Not a stub of the client. The REAL ``OpenAIChatCompletionClient`` and the
REAL MAF harness agent make each call, and only the network is replaced
(``openai.DefaultAsyncHttpxClient`` gets an ``httpx.MockTransport``). So the
URL, the headers and the body that a test asserts are the ones that would
leave the process for the gateway, which is the Router (D56).

A test scripts the answers in order. Each answer is the text of an
assistant message, or a list of tool calls ``[(name, {arguments})]``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest


@dataclass
class FakeGateway:
    answers: list[Any] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def say(self, *answers: Any) -> FakeGateway:
        self.answers.extend(answers)
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8") or "{}")
        self.requests.append({
            "url": str(request.url),
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "body": body,
        })
        answer = self.answers.pop(0) if self.answers else "(no scripted answer)"
        return httpx.Response(200, json=_completion(answer, len(self.requests)))

    # ── views ───────────────────────────────────────────────────────────────

    def tool_names(self, index: int = 0) -> set[str]:
        """The tool names that request *index* offered the model."""
        tools = self.requests[index]["body"].get("tools") or []
        return {t["function"]["name"] for t in tools}

    def tool_results(self, index: int) -> list[str]:
        """The tool result texts that request *index* sent back to the model."""
        return [
            str(m.get("content"))
            for m in self.requests[index]["body"].get("messages", [])
            if m.get("role") == "tool"
        ]

    def last_user_text(self, index: int) -> str:
        users = [
            m for m in self.requests[index]["body"].get("messages", [])
            if m.get("role") == "user"
        ]
        content = users[-1].get("content") if users else ""
        if isinstance(content, list):
            return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        return str(content or "")


def _completion(answer: Any, n: int) -> dict[str, Any]:
    if isinstance(answer, list):
        calls = [
            {
                "id": f"call_{n}_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for i, (name, args) in enumerate(answer)
        ]
        message: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": calls}
        finish = "tool_calls"
    elif isinstance(answer, tuple):
        # (text, [(name, args)]): narration beside a tool call, as the spike saw.
        text, tool_calls = answer
        message = _completion(tool_calls, n)["choices"][0]["message"]
        message["content"] = text
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": str(answer)}
        finish = "stop"
    return {
        "id": f"cmpl-{n}", "object": "chat.completion", "created": 1, "model": "tier-balanced",
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    """Every ``AsyncOpenAI`` built from here on reaches only this fake."""
    import openai

    fake = FakeGateway()
    real = openai.DefaultAsyncHttpxClient
    monkeypatch.setattr(
        openai, "DefaultAsyncHttpxClient",
        lambda **kw: real(transport=httpx.MockTransport(fake.handler), **kw),
    )
    return fake
