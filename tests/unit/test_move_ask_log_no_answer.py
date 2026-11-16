"""Task 3538: a failing ``move_ask`` never writes the member's answer to the log.

The Opus review of PR 2453 found that ``_late_answer_from_row`` logged with
``exc_info=True`` when ``pending_ask.move_ask`` failed. No engine sets
``hide_parameters``, so a database fault can echo the statement parameters, and one
of them is the answer (a card edit or typed text). The three handlers that carry the
answer now log the error class and the ask id only.

Fence (R7): the behaviour test below, and the source test that bans ``exc_info`` in the
three handlers that hold an answer as a parameter.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

agent_routes = pytest.importorskip("gateway.routes.agent", reason="gateway not installed")
pending_ask = pytest.importorskip("orchestrator.pending_ask")

SECRET = "my private edit: salary 99,999 and the code word kumquat"


async def test_a_failing_move_ask_logs_class_and_id_only(monkeypatch) -> None:
    import structlog
    from gateway import chat_recovery

    monkeypatch.setattr(pending_ask, "durable_asks_enabled", lambda: True)
    monkeypatch.setattr(pending_ask, "read_ask", lambda org, rid: {"actor_email": "a@x.test", "thread_id": "t"})

    async def resend(thread_id: str, row: Any, answer: str) -> Any:
        return SimpleNamespace()

    def boom(*_a: Any, **_k: Any) -> None:
        # What a database driver can do: put the statement parameters in the message.
        raise RuntimeError(f"INSERT failed with parameters ({SECRET!r},)")

    monkeypatch.setattr(chat_recovery, "card_answer_from_ask", resend)
    monkeypatch.setattr(pending_ask, "move_ask", boom)
    req = SimpleNamespace(request_id="req-123456789012345", thread_id="t", answer=SECRET)
    user = SimpleNamespace(organization_id="org-a", email="a@x.test")
    with structlog.testing.capture_logs() as logs:
        result = await agent_routes._late_answer_from_row(req, user)
    assert result == (None, True)
    failed = [x for x in logs if x.get("event") == "agent.pending_ask_answer_failed"]
    assert len(failed) == 1
    line = failed[0]
    assert line["error"] == "RuntimeError" and line["request_id"] == "req-12345678"
    assert "exc_info" not in line, "exc_info writes the exception text, and the text can hold the answer"
    assert SECRET not in repr(logs) and "kumquat" not in repr(logs)


@pytest.mark.parametrize("name", ["_late_answer_from_row", "_resume_from_row", "_give_back_claim"])
def test_the_handlers_that_hold_an_answer_never_log_exc_info(name) -> None:
    tree = ast.parse(inspect.getsource(getattr(agent_routes, name)).lstrip())
    bad = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "warning"
        and n.args and isinstance(n.args[0], ast.Constant)
        and n.args[0].value in ("agent.pending_ask_answer_failed", "agent.resume_claim_failed",
                                "agent.resume_claim_restore_failed")
        and any(k.arg == "exc_info" for k in n.keywords)
    ]
    assert bad == [], f"exc_info in {name} at {bad}"


def test_settle_never_logs_exc_info() -> None:
    """The same leak in ``pending_ask.settle`` (found by the verifier of PR 2519)."""
    tree = ast.parse(inspect.getsource(pending_ask.settle).lstrip())
    assert "answer" in inspect.signature(pending_ask.settle).parameters
    bad = [n.lineno for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "warning"
           and any(k.arg == "exc_info" for k in n.keywords)]
    assert bad == []
