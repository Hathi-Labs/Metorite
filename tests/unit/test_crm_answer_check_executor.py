"""The CRM number check through the REAL executor (WS-53 CRM-Q29, fix round).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q29.

``crm-assistant`` runs through ``run_agent_stream`` with its real factory and
its real MAF client. Only the HTTP transport under the client is a script,
and ``crm_tools._get`` answers in place of the gateway. So the run input is
the string that production sends (``MAF_NATIVE_SESSIONS`` off), and the idle
watchdog is the real one.

* fix 1, the packed history is no source ->
  ``test_an_invented_number_of_an_earlier_answer_is_caught``;
* fix 1, the member's own words are a source ->
  ``test_the_member_s_own_words_are_a_source``;
* fix 3, a slow held answer outlives the idle watchdog ->
  ``test_a_slow_held_answer_outlives_the_idle_watchdog``.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from tests.unit._native_maf_harness import (
    _a_tenant,  # noqa: F401 — a fixture, used by name
    drive_native,
    tool_turn,
)

pytest.importorskip("agent_framework", reason="agent_framework not installed")

AGENT = ("crm-assistant", "apps/agents/agent-crm")

AGGREGATE = {
    "groups": [{"label": "Open", "value": 1234567}, {"label": "Won", "value": 500000}],
    "total": 2100000,
}


class SlowModel:
    """An async transport handler. Request *i* plays ``turns[i]``.

    A text turn streams its text in ``pieces`` chunks, ``gap`` seconds apart,
    so a model that writes slowly sends an update often.
    """

    def __init__(self, turns: list[Any], *, pieces: int = 2, gap: float = 0.0) -> None:
        self.turns = turns
        self.pieces = pieces
        self.gap = gap
        self.bodies: list[dict[str, Any]] = []

    def _chunks(self, index: int, turn: Any) -> list[dict[str, Any]]:
        if isinstance(turn, str):
            size = max(1, len(turn) // self.pieces + 1)
            parts = [turn[i:i + size] for i in range(0, len(turn), size)]
            deltas = [{"role": "assistant", "content": p} for p in parts]
            deltas.append({"finish_reason": "stop"})
        else:
            deltas = turn
        out = []
        for part in deltas:
            finish = part.get("finish_reason")
            delta = {k: v for k, v in part.items() if k != "finish_reason"}
            out.append({
                "id": f"chatcmpl-{index}", "object": "chat.completion.chunk",
                "created": 0, "model": "probe",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            })
        return out

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        index = len(self.bodies)
        self.bodies.append(body)
        chunks = self._chunks(index, self.turns[min(index, len(self.turns) - 1)])
        slow = isinstance(self.turns[min(index, len(self.turns) - 1)], str)
        gap = self.gap

        async def _stream() -> AsyncIterator[bytes]:
            for chunk in chunks:
                if slow and gap:
                    await asyncio.sleep(gap)
                yield f"data: {json.dumps(chunk)}\n\n".encode()
            yield b"data: [DONE]\n\n"

        return httpx.Response(
            200, content=_stream(), headers={"content-type": "text/event-stream"},
        )


@pytest.fixture
def _crm(monkeypatch):
    """The gateway answers the CRM reads, and the check is ``on``."""
    from acb_common import get_settings
    from acb_skills import crm_tools

    async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/crm/aggregate":
            return AGGREGATE
        if path == "/crm/mode":
            return {"mode": "native"}
        return {}

    monkeypatch.setattr(crm_tools, "_get", _get)
    monkeypatch.setattr(get_settings(), "crm_answer_check", "on")
    yield


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(
        str(e.get("delta") or "") for e in events if e.get("type") == "TEXT_MESSAGE_CONTENT"
    )


_CALL = tool_turn("crm_aggregate", json.dumps(
    {"entity": "deals", "measure": "sum_amount", "group_by": "status"}))


@pytest.mark.usefixtures("_a_tenant", "_crm")
def test_an_invented_number_of_an_earlier_answer_is_caught(monkeypatch) -> None:
    model = SlowModel([_CALL, "You still have ₹99 lakh open.", "You have INR 12,34,567 open."])
    events, _ = drive_native(
        *AGENT, monkeypatch, model, message="And now?",
        history=[{"role": "user", "content": "What is open?"},
                 {"role": "assistant", "content": "You had ₹99 lakh open."}],
    )
    # The run input is the packed string, and it holds the earlier answer.
    first = json.dumps(model.bodies[0], ensure_ascii=False)
    assert "₹99 lakh" in first
    assert _text(events) == "You have INR 12,34,567 open."
    assert len(model.bodies) == 3
    assert "₹99 lakh" in json.dumps(model.bodies[2]["messages"][-1], ensure_ascii=False)


@pytest.mark.usefixtures("_a_tenant", "_crm")
def test_the_member_s_own_words_are_a_source(monkeypatch) -> None:
    answer = "The deals over ₹7 lakh hold INR 12,34,567."
    model = SlowModel([_CALL, answer])
    events, _ = drive_native(*AGENT, monkeypatch, model,
                             message="Which deals are over ₹7 lakh?")
    assert _text(events) == answer
    assert len(model.bodies) == 2


@pytest.mark.usefixtures("_a_tenant", "_crm")
def test_a_slow_held_answer_outlives_the_idle_watchdog(monkeypatch) -> None:
    from acb_skills import answer_check
    from orchestrator import executor
    from orchestrator.watchdog import WatchdogPolicy

    monkeypatch.setattr(executor, "_WATCHDOG", WatchdogPolicy(idle=0.6))
    monkeypatch.setattr(answer_check, "HEARTBEAT_S", 0.1)
    answer = "Open deals hold INR 12,34,567, and won deals hold INR 5,00,000."
    # Eight chunks, 0.2 s apart: 1.6 s in all, far past the 0.6 s bound.
    model = SlowModel([_CALL, answer], pieces=8, gap=0.2)
    events, _ = drive_native(*AGENT, monkeypatch, model, message="What is open?")
    assert _text(events) == answer
    assert not [e for e in events if e.get("type") == "RUN_ERROR"]
    # The sign of life resets the watchdog, and the member never sees it.
    assert not [e for e in events if e.get("name") == answer_check.HOLD_EVENT]
