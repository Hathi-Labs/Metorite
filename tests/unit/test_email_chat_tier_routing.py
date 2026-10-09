"""The email chat route reads no `chat_model`. WS-45 S4b, then D-EM-61.

Spec: ``project-docs/specs/ai_tier_routing.md`` §8 (the
``email_assistant_settings.chat_model`` row) and §11 S4 done-when 2.
``project-docs/specs/email_app_master_plan.md`` §10.4.16 (EM-T15, D-EM-61).

``POST /email/automation/ai/chat`` runs the email-assistant. For an agent
that ``AI_TIER_ROUTING`` covers, the platform picks the tier of each step, so
the route sends no ``model``. For every other case it sends the chat tier
that our code chooses, ``EMAIL_TASK_TIERS["chat"]``. Since D-EM-61 (owner,
2026-10-09) no case reads the stored ``chat_model``. The column stays (R6,
contract later).

Hermetic: the subject is which read the route makes and what it hands the
executor. ``_build_chat_context`` is replaced, so the only session the route
could open is the old settings read, and the spy counts it. R8 binds
nothing here: ``test_email_chat_context_owner.py`` runs the route on a real
database with a stored ``chat_model``.

Mutations this file catches (R7):

* the route sends a model for a covered agent
  -> ``test_a_covered_agent_sends_no_model``;
* the route reads the stored ``chat_model`` again, or sends another tier
  -> ``test_an_uncovered_agent_sends_the_fixed_tier``.
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
    from gateway.routes.email.automation import chat as chat_mod

    if flag is None:
        monkeypatch.delenv("AI_TIER_ROUTING", raising=False)
    else:
        monkeypatch.setenv("AI_TIER_ROUTING", flag)
    get_settings.cache_clear()

    seen: dict[str, Any] = {"sessions": 0}

    async def _context(user_id, account_id, email_context_id, first):
        return ACCOUNT, ["ctx"]

    class _Session:
        async def __aenter__(self):
            seen["sessions"] += 1
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
    monkeypatch.setattr(executor_mod, "run_agent_stream", _stream)
    user = UserContext(email="member@example.com", role=UserRole.EMPLOYEE,
                       organization_id="org-email-tier")
    req = chat_mod.AIChatRequest(
        messages=[{"role": "user", "content": "what is in my inbox?"}],
        account_id=ACCOUNT)
    asyncio.run(chat_mod.ai_chat(req, user, BackgroundTasks()))
    get_settings.cache_clear()
    return seen


def test_an_uncovered_agent_sends_the_fixed_tier(monkeypatch) -> None:
    for flag in (None, "projects-assistant"):
        seen = _run(monkeypatch, flag)
        assert seen["sessions"] == 0, (
            f"{flag}: the route opened a session to read the stored chat model")
        assert seen["agent"] == "email-assistant"
        assert seen["kw"]["model"] == "tier-powerful", flag


def test_a_covered_agent_sends_no_model(monkeypatch) -> None:
    for flag in ("email-assistant", "*"):
        seen = _run(monkeypatch, flag)
        assert seen["sessions"] == 0, flag
        assert seen["kw"]["model"] is None, flag
