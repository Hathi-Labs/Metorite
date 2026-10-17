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


# ── Fix round 3: the hold beat never leaves a relay ─────────────────────────

def test_a_relay_drops_the_hold_beat() -> None:
    from acb_skills import answer_check
    from orchestrator import executor

    beat = {"type": "CUSTOM", "name": answer_check.HOLD_EVENT, "value": {}}
    assert not executor._relayable(beat)
    assert not executor._relayable(None)
    assert executor._relayable({"type": "CUSTOM", "name": "artifact_created"})


def _queue_reads(func: Any) -> set[str]:
    """Each name that *func* binds from a run queue read."""
    import ast

    names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        reads = [n for n in ast.walk(node.value) if isinstance(n, ast.Attribute)
                 and n.attr in ("get_nowait", "get")]
        if reads:
            names.add(target.id)
    return names


def _drops_the_beat(test: Any, name: str, *, in_body: bool) -> bool:
    """True when the branch of *test* keeps a beat out. In the body of the
    ``if``, that is ``_relayable(name)`` or ``not _is_hold_beat(name)``. In
    its ``else``, or before a ``continue``, it is the reverse."""
    import ast

    negated: set[int] = set()
    for node in ast.walk(test):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            negated.update(id(n) for n in ast.walk(node.operand))
    for call in (n for n in ast.walk(test) if isinstance(n, ast.Call)):
        fn = getattr(call.func, "id", "")
        if name not in [getattr(a, "id", None) for a in call.args]:
            continue
        keeps = (fn == "_relayable") != (id(call) in negated)
        if fn == "_is_hold_beat":
            keeps = id(call) in negated
        if fn in ("_relayable", "_is_hold_beat") and keeps == in_body:
            return True
    return False


def _guarded(node: Any, name: str, parents: dict[Any, Any]) -> bool:
    """True when a hold beat check of *name* keeps a beat away from *node*."""
    import ast

    child, up = node, parents.get(node)
    while up is not None and not isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if isinstance(up, ast.If):
            in_body = any(child is n for n in up.body)
            if _drops_the_beat(up.test, name, in_body=in_body):
                return True
        # A sibling before it: "if not x or _is_hold_beat(x): continue".
        for field in ("body", "orelse"):
            block = getattr(up, field, None)
            if isinstance(block, list) and child in block:
                for prior in block[:block.index(child)]:
                    if (isinstance(prior, ast.If)
                            and any(isinstance(b, ast.Continue) for b in prior.body)
                            and _drops_the_beat(prior.test, name, in_body=False)):
                        return True
        child, up = up, parents.get(up)
    return False


def _relay_sites(tree: Any) -> list[tuple[int, bool]]:
    """Each ``yield _sse(x)`` of an event read from a run queue, with true
    when a hold beat check guards it."""
    import ast

    parents: dict[Any, Any] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    sites: list[tuple[int, bool]] = []
    for func in (n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        reads = _queue_reads(func)
        for node in ast.walk(func):
            call = getattr(node, "value", None)
            if not (isinstance(node, ast.Yield) and isinstance(call, ast.Call)):
                continue
            if getattr(call.func, "id", "") != "_sse" or len(call.args) != 1:
                continue
            arg = call.args[0]
            if isinstance(arg, ast.Name) and arg.id in reads:
                sites.append((node.lineno, _guarded(node, arg.id, parents)))
    return sites


def test_each_relay_of_a_run_queue_drops_the_hold_beat() -> None:
    """Fix round 3, rule 3: each ``yield _sse(x)`` of an event read from a
    run queue sits behind a hold beat check: the native loop, Tier 1.5 and
    Tier 2. A delegated native run puts its beat on its parent's queue, so a
    relay that forgets the check sends it to the member."""
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / (
        "apps/services/orchestrator/orchestrator/executor.py")
    sites = _relay_sites(ast.parse(path.read_text(encoding="utf-8-sig")))
    assert len(sites) >= 8, sites   # 3 native, 3 at Tier 1.5, 2 at Tier 2
    assert [line for line, ok in sites if not ok] == []
