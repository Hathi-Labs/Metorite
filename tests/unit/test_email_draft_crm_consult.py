"""The email draft consults ``crm-assistant`` as the member (WS-53 CRM-C7).

Spec: ``project-docs/specs/crm_platform.md`` §20.10, the item 6 exception.
The drafter asks the CRM agent for facts that the thread lacks. A CRM tool
refuses a run with no member, so the consult must carry the member's own
email in ``user_email``, never the memory scope of the inbox.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from acb_skills import memory_tools
from gateway.routes.email.automation import drafting

_EMAIL = {"from": "alice@example.com", "from_name": "Alice", "subject": "Quote",
          "body": "Where is the order you quoted us?"}


async def test_the_crm_consult_carries_the_members_email(monkeypatch) -> None:
    import orchestrator.executor as executor

    calls: list[tuple[str, dict]] = []

    async def fake_run_agent(agent: str, payload: dict, *_a: Any, **_k: Any) -> dict:
        calls.append((agent, dict(payload)))
        return {"answer": "Deal Acme AMC is in Negotiation."}

    async def plan(*_a: Any, **_k: Any) -> list[dict[str, str]]:
        return [{"agent": "crm-assistant", "question": "Where is the Acme deal?"}]

    monkeypatch.setattr(executor, "run_agent", fake_run_agent)
    monkeypatch.setattr(drafting, "_draft_consult_plan", plan)
    monkeypatch.setattr(memory_tools, "remember", AsyncMock(return_value="no relevant"))
    draft = AsyncMock(return_value="Hi Alice")
    monkeypatch.setattr(drafting, "_llm_draft_reply", draft)

    out = await asyncio.wait_for(drafting._orchestrate_draft(
        _EMAIL, "", "", "owner@a.test", account_id="acct-1"), 5.0)

    assert out == "Hi Alice"
    assert calls == [("crm-assistant", {
        "message": "Where is the Acme deal?", "user_email": "owner@a.test",
    })]
    context = draft.await_args.kwargs.get("context", "")
    assert "Negotiation" in context


async def test_the_plan_keeps_a_crm_consult(monkeypatch) -> None:
    """The plan names ``crm-assistant`` and passes it through its filter."""
    async def llm(*_a: Any, **_k: Any) -> tuple[dict, str, str]:
        return ({"kind": "sales/commercial", "consult": [
            {"agent": "crm-assistant", "question": "Deal stage?"},
            {"agent": "someone-else", "question": "x"},
        ]}, "", "tier-fast")

    monkeypatch.setattr(drafting, "_llm_json", llm)
    out = await drafting._draft_consult_plan(_EMAIL)
    assert out == [{"agent": "crm-assistant", "question": "Deal stage?"}]
