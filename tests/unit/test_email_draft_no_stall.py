"""EM-T17: drafting never stalls (``email_app_master_plan.md`` §17).

PR-A2 owns clause A7 here. PR-A (the server bounds) adds its own clauses to
this file, so the second of the two PRs to merge resolves it.

A7: the reply path sends the ``context`` ``done`` event. The draft of an
empty reply goes to the reply drafter, and the run used to return before it
sent ``context`` ``done``. The composer then drew "Thread context" as a step
that ran for the whole draft.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gateway.routes.email.automation import drafting as drafting_mod

from tests.unit._email_fakes import bind_db

_CTX = {
    "from": "ravi@example.com", "from_name": "Ravi", "subject": "Quote",
    "body": "Please send it.", "thread": "", "thread_id": "t1",
    "self": "sales@example.com", "self_label": "Sales",
}


def _wire(monkeypatch, *, order: list[str]) -> None:
    async def _agent_draft(*_a, **_kw) -> str:
        order.append("draft")
        return "Hello Ravi"

    async def _compose(**_kw) -> str:
        order.append("draft")
        return "Hello Ravi"

    for name, value in (
        ("_tenant_session", bind_db(AsyncMock())),
        ("_assert_account_owner", AsyncMock()),
        ("_build_reply_context", AsyncMock(return_value=dict(_CTX))),
        ("_load_assistant_about", AsyncMock(return_value=("", ""))),
        ("_agent_draft_reply", _agent_draft),
        ("_llm_compose_assist", _compose),
        ("_store_ai_draft", AsyncMock()),
    ):
        monkeypatch.setattr(drafting_mod, name, value)


def _context_events(events: list[dict]) -> list[str]:
    return [e.get("status", "") for e in events
            if e.get("kind") == "stage" and e.get("id") == "context"]


async def test_the_reply_path_sends_the_context_done_event(monkeypatch) -> None:
    """A7: an empty reply goes to the reply drafter. The run sends
    ``context`` ``done`` once, and before the draft starts."""
    order: list[str] = []
    events: list[dict] = []

    async def on_activity(evt: dict) -> None:
        events.append(evt)
        if evt.get("id") == "context":
            order.append(f"context:{evt.get('status')}")

    _wire(monkeypatch, order=order)
    out = await drafting_mod._compose_assist_run(
        drafting_mod.ComposeAssistRequest(
            account_id="acc-1", mode="reply", message_id="m1"),
        SimpleNamespace(email="me@example.com"),
        on_activity=on_activity)

    assert out["draft"].startswith("Hello Ravi")
    assert _context_events(events) == ["start", "done"]
    assert order == ["context:start", "context:done", "draft"]


@pytest.mark.parametrize("mode,body", [
    ("reply", "My own words."),
    ("forward", ""),
    ("new", ""),
])
async def test_each_other_path_sends_context_done_once(
        monkeypatch, mode: str, body: str) -> None:
    """The improve, forward and new paths keep their one ``context``
    ``done``. The A7 change adds no second one."""
    order: list[str] = []
    events: list[dict] = []

    async def on_activity(evt: dict) -> None:
        events.append(evt)

    _wire(monkeypatch, order=order)
    monkeypatch.setattr(drafting_mod, "resolve_self", AsyncMock(
        return_value=SimpleNamespace(address="sales@example.com", label="Sales")))
    await drafting_mod._compose_assist_run(
        drafting_mod.ComposeAssistRequest(
            account_id="acc-1", mode=mode, body=body,
            message_id="m1" if mode != "new" else None,
            to=["ravi@example.com"]),
        SimpleNamespace(email="me@example.com"),
        on_activity=on_activity)

    assert _context_events(events) == ["start", "done"]
