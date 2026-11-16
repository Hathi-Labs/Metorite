"""CSY-2 (task 3401): one ``mcp.call`` log line for each MCP call, on every gate path.

Spec: ``project-docs/specs/chat_connected_systems.md`` §3.3.

* **A1** Each call logs exactly one line: the read-only pass, the card path
  (approved, denied, refused) and a refusal by policy or by unreadable arguments.
* **A2** No line holds an argument value, a result, a header or a URL path.
* **A3** The outcome comes from a closed list. A new outcome string fails
  ``test_every_outcome_the_gate_logs_is_in_the_closed_list``.
* ``source`` is ``chat`` for a chat run and ``job`` for any other run.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

pytest.importorskip("agent_framework", reason="agent_framework not installed")
pytest.importorskip("mcp", reason="mcp not installed")

from acb_common.db import clear_tenant
from orchestrator import _maf_mcp, executor

SECRET_ARG = "sk-csy2-secret-argument-value"


@pytest.fixture(autouse=True)
def _clean():
    clear_tenant()
    executor._pending_user_input.clear()
    yield
    clear_tenant()
    executor._pending_user_input.clear()


def _function(name: str = "srv_do", remote: str = "do") -> Any:
    from agent_framework import FunctionTool

    return FunctionTool(
        name=name, func=lambda **_k: "ran", description="d",
        additional_properties={"_mcp_is_tool": True, "_mcp_remote_name": remote},
    )


def _gate(fn: Any, hints: dict[str, Any] | None, *, member: bool = True) -> Any:
    remote = fn.additional_properties["_mcp_remote_name"]
    server = SimpleNamespace(
        name="srv", _functions=[fn],
        _ws8c_tool_meta={remote: _maf_mcp.ToolMeta(hints=hints, description="")})
    return _maf_mcp.McpPermissionGate(tools=[server], member_present=member)


def _ctx(fn: Any) -> Any:
    return SimpleNamespace(function=fn, arguments={"record_id": SECRET_ARG}, result=None)


class _Ran:
    def __init__(self, exc: BaseException | None = None) -> None:
        self.count = 0
        self.exc = exc

    async def __call__(self) -> None:
        self.count += 1
        if self.exc is not None:
            raise self.exc


@contextlib.contextmanager
def _card_channel():
    queue: asyncio.Queue[Any] = asyncio.Queue()
    token = executor._active_run_queue.set(queue)
    try:
        yield queue
    finally:
        executor._active_run_queue.reset(token)


async def _answer_next(queue: asyncio.Queue[Any], answer: str) -> None:
    while True:
        event = await asyncio.wait_for(queue.get(), timeout=5)
        if event and event.get("name") == "confirmation_requested":
            executor._pending_user_input[event["value"]["request_id"]].set_result({"answer": answer})
            return


def _calls(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [x for x in logs if x.get("event") == "mcp.call"]


async def _run(gate: Any, fn: Any, ran: _Ran, answer: str | None = None) -> tuple[list[dict], Any]:
    ctx = _ctx(fn)
    with _card_channel() as queue, structlog.testing.capture_logs() as logs:
        task = asyncio.create_task(gate.process(ctx, ran))
        if answer is not None:
            await _answer_next(queue, answer)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, timeout=5)
    return _calls(logs), ctx


async def test_a_read_only_call_logs_one_ok_line() -> None:
    fn = _function()
    calls, _ = await _run(_gate(fn, {"readOnlyHint": True}), fn, _Ran())
    assert len(calls) == 1
    line = calls[0]
    assert line["outcome"] == "ok" and line["read_only"] is True
    assert line["server"] == "srv" and line["tool"] == "do"
    assert isinstance(line["ms"], int) and line["source"] == "chat"


async def test_a_card_approved_call_logs_card_approved() -> None:
    fn = _function()
    calls, _ = await _run(_gate(fn, None), fn, _Ran(), answer="APPROVE")
    assert [c["outcome"] for c in calls] == ["card_approved"]
    assert calls[0]["read_only"] is False


async def test_a_card_denied_call_logs_card_denied_and_never_runs() -> None:
    fn = _function()
    ran = _Ran()
    calls, _ = await _run(_gate(fn, None), fn, ran, answer="REJECT")
    assert [c["outcome"] for c in calls] == ["card_denied"] and ran.count == 0


async def test_a_run_with_no_member_logs_card_refused() -> None:
    fn = _function()
    calls, _ = await _run(_gate(fn, None, member=False), fn, _Ran())
    assert [c["outcome"] for c in calls] == ["card_refused"]


async def test_unreadable_arguments_log_refused() -> None:
    fn = _function()
    ctx = SimpleNamespace(function=fn, arguments=object(), result=None)
    with structlog.testing.capture_logs() as logs:
        await _gate(fn, {"readOnlyHint": True}).process(ctx, _Ran())
    assert [c["outcome"] for c in _calls(logs)] == ["refused"]


async def test_a_policy_refusal_logs_refused(monkeypatch) -> None:
    import orchestrator._tool_injection as ti

    monkeypatch.setattr(ti, "_permission_gate", lambda *a, **k: (False, "policy"))
    fn = _function()
    ran = _Ran()
    calls, ctx = await _run(_gate(fn, {"readOnlyHint": True}), fn, ran)
    assert [c["outcome"] for c in calls] == ["refused"] and ran.count == 0


async def test_a_tool_that_raises_logs_error_and_still_raises() -> None:
    fn = _function()
    ctx = _ctx(fn)
    with structlog.testing.capture_logs() as logs, pytest.raises(RuntimeError):
        await _gate(fn, {"readOnlyHint": True}).process(ctx, _Ran(RuntimeError("boom")))
    assert [c["outcome"] for c in _calls(logs)] == ["error"]


async def test_a_tool_that_times_out_logs_timeout() -> None:
    fn = _function()
    ctx = _ctx(fn)
    with structlog.testing.capture_logs() as logs, pytest.raises(asyncio.TimeoutError):
        await _gate(fn, {"readOnlyHint": True}).process(ctx, _Ran(asyncio.TimeoutError()))
    assert [c["outcome"] for c in _calls(logs)] == ["timeout"]


async def test_a_function_that_is_not_mcp_logs_nothing() -> None:
    from agent_framework import FunctionTool

    fn = FunctionTool(name="plain", func=lambda **_k: "x", description="d")
    ctx = _ctx(fn)
    with structlog.testing.capture_logs() as logs:
        await _maf_mcp.McpPermissionGate().process(ctx, _Ran())
    assert _calls(logs) == []


async def test_no_line_holds_an_argument_a_result_or_a_url() -> None:
    fn = _function()
    calls, _ = await _run(_gate(fn, {"readOnlyHint": True}), fn, _Ran())
    assert set(calls[0]) <= {"event", "log_level", "server", "tool", "outcome", "ms",
                             "read_only", "org", "source", "run_id"}
    assert SECRET_ARG not in repr(calls)


@pytest.mark.parametrize(("ctx_source", "expected"),
                         [("chat", "chat"), ("shell", "chat"), (None, "chat"),
                          ("cron", "job"), ("poll", "job"), ("workflow", "job")])
def test_the_source_is_chat_only_for_a_chat_run(ctx_source, expected) -> None:
    structlog.contextvars.clear_contextvars()
    if ctx_source:
        structlog.contextvars.bind_contextvars(source=ctx_source)
    try:
        with structlog.testing.capture_logs() as logs:
            _maf_mcp.log_mcp_call(server="s", tool="t", outcome="ok", read_only=True, ms=1)
    finally:
        structlog.contextvars.clear_contextvars()
    assert _calls(logs)[0]["source"] == expected


def test_an_outcome_outside_the_list_is_logged_as_error() -> None:
    with structlog.testing.capture_logs() as logs:
        _maf_mcp.log_mcp_call(server="s", tool="t", outcome="weird", read_only=False, ms=1)
    assert _calls(logs)[0]["outcome"] == "error"


def test_every_outcome_the_gate_logs_is_in_the_closed_list() -> None:
    """A new outcome string in the gate fails here (R7)."""
    src = Path(_maf_mcp.__file__).read_text(encoding="utf-8")
    seen: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].value, ast.Name) and node.targets[0].value.id == "rec"
                and isinstance(node.targets[0].slice, ast.Constant) and node.targets[0].slice.value == "outcome"
                and isinstance(node.value, ast.Constant)):
            seen.add(node.value.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "rec"):
            for kw in node.keywords:
                if kw.arg == "outcome" and isinstance(kw.value, ast.Constant):
                    seen.add(kw.value.value)
    assert seen and seen <= set(_maf_mcp.MCP_CALL_OUTCOMES), seen - set(_maf_mcp.MCP_CALL_OUTCOMES)
    assert set(_maf_mcp.MCP_CALL_OUTCOMES) == {"ok", "error", "timeout", "refused",
                                               "card_approved", "card_denied", "card_refused"}
