"""The email chat route reads no `chat_model` for a covered agent. WS-45 S4b.

Spec: ``project-docs/specs/ai_tier_routing.md`` §8 (the
``email_assistant_settings.chat_model`` row) and §11 S4 done-when 2.

``POST /email/automation/ai/chat`` runs the email-assistant. For an agent
that ``AI_TIER_ROUTING`` covers, the platform picks the tier of each step, so
the route reads no ``chat_model`` and sends no ``model``. For every other
case it reads the account's ``chat_model`` and sends it, as on main. The
column stays (R6, contract later).

Hermetic: the subject is which read the route makes and what it hands the
executor. ``_account_models`` is replaced by a spy, so no SQL runs, and R8
binds nothing here. The SQL of ``_account_models`` is unchanged and is
proved by ``test_email_chat_context_owner.py`` on a real database.

Mutations this file catches (R7):

* the route reads ``chat_model`` for a covered agent
  -> ``test_a_covered_agent_reads_no_chat_model``;
* the route stops reading it for an uncovered agent, or sends another model
  -> ``test_an_uncovered_agent_reads_and_sends_it_as_before``.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("fastapi")

from acb_auth.roles import UserContext, UserRole
from acb_common.settings import get_settings
from fastapi import BackgroundTasks

ACCOUNT = "00000000-0000-0000-0000-0000000000a1"


def _run(monkeypatch, flag: str | None) -> dict[str, Any]:
    import orchestrator.executor as executor_mod
    from gateway.routes.email.automation import assistant as assistant_mod
    from gateway.routes.email.automation import chat as chat_mod

    if flag is None:
        monkeypatch.delenv("AI_TIER_ROUTING", raising=False)
    else:
        monkeypatch.setenv("AI_TIER_ROUTING", flag)
    get_settings.cache_clear()

    seen: dict[str, Any] = {"reads": []}

    async def _context(user_id, account_id, email_context_id, first):
        return ACCOUNT, ["ctx"]

    async def _models(db, account_id):
        seen["reads"].append(account_id)
        return {"chat": "tier-fast", "draft": "tier-powerful", "compose": "tier-fast"}

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_a):
            return False

    def _stream(agent, payload, **kw):
        seen["agent"] = agent
        seen["kw"] = kw

        async def _gen():
            if False:  # pragma: no cover - an empty stream
                yield ""
        return _gen()

    monkeypatch.setattr(chat_mod, "_build_chat_context", _context)
    monkeypatch.setattr(chat_mod, "_tenant_session", lambda: _Session())
    monkeypatch.setattr(assistant_mod, "_account_models", _models)
    monkeypatch.setattr(executor_mod, "run_agent_stream", _stream)
    user = UserContext(email="member@example.com", role=UserRole.EMPLOYEE,
                       organization_id="org-email-tier")
    req = chat_mod.AIChatRequest(
        messages=[{"role": "user", "content": "what is in my inbox?"}],
        account_id=ACCOUNT)
    asyncio.run(chat_mod.ai_chat(req, user, BackgroundTasks()))
    get_settings.cache_clear()
    return seen


def test_an_uncovered_agent_reads_and_sends_it_as_before(monkeypatch) -> None:
    for flag in (None, "projects-assistant"):
        seen = _run(monkeypatch, flag)
        assert seen["reads"] == [ACCOUNT], flag
        assert seen["agent"] == "email-assistant"
        assert seen["kw"]["model"] == "tier-fast", flag


def test_a_covered_agent_reads_no_chat_model(monkeypatch) -> None:
    for flag in ("email-assistant", "*"):
        seen = _run(monkeypatch, flag)
        assert seen["reads"] == [], flag
        assert seen["kw"]["model"] is None, flag
