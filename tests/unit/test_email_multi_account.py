"""Unit tests for per-account isolation across a user's multiple mailboxes.

Two seams are covered:

1. ``email_memory_scope`` — the Mem0 key namespacing that stops one inbox's
   learned writing style / reply preferences from leaking into another's
   drafting. Reads and writes MUST agree on the key, so the helper is the single
   source of truth and is exercised directly + through ``_orchestrate_draft``.

2. ``EmailAccountModel.is_default`` — the per-user default-mailbox flag the UI
   uses for its initial selection.
"""
from __future__ import annotations

import asyncio

from unittest.mock import patch

from gateway.routes import email as m
from gateway.routes.email.core import email_memory_scope
from gateway.routes.email.transport.accounts import EmailAccountModel

_drafting = m.automation.drafting


async def _drain_background_tasks() -> None:
    """The Mem0 write is fire-and-forget (_spawn_background) so the draft
    returns without waiting on it — the tests must drain it before asserting
    the write happened (and under which scope)."""
    while _drafting._BG_TASKS:
        await asyncio.gather(*list(_drafting._BG_TASKS), return_exceptions=True)


# ── email_memory_scope ───────────────────────────────────────────────────────

def test_memory_scope_namespaces_by_account() -> None:
    assert email_memory_scope("me@acme.com", "acc-1") == "me@acme.com#acct:acc-1"
    # Different accounts → different keys (the whole point: no cross-inbox leak).
    assert email_memory_scope("me@acme.com", "acc-1") != email_memory_scope(
        "me@acme.com", "acc-2")


def test_memory_scope_is_case_insensitive_on_email() -> None:
    # The owner email can arrive in any case; the key must be stable so a write
    # and a later read collide on the same Mem0 user_id.
    assert email_memory_scope("Me@Acme.com", "acc-1") == "me@acme.com#acct:acc-1"


def test_memory_scope_falls_back_to_bare_email() -> None:
    # No account resolved → legacy/global scope (bare email), never a dangling
    # "#acct:" suffix.
    assert email_memory_scope("me@acme.com", None) == "me@acme.com"
    assert email_memory_scope("me@acme.com", "") == "me@acme.com"
    assert email_memory_scope("me@acme.com", "   ") == "me@acme.com"


def test_memory_scope_empty_email() -> None:
    assert email_memory_scope("", "acc-1") == ""
    assert email_memory_scope("", None) == ""


# ── _orchestrate_draft scopes BOTH the read and the write to the account ─────

async def test_orchestrate_draft_scopes_memory_per_account() -> None:
    captured: dict[str, list[str]] = {"set_uid": [], "add_uid": []}

    async def fake_remember(_q: str) -> str:
        return "(no relevant memories found)"

    def fake_set(uid: str) -> None:
        captured["set_uid"].append(uid)

    async def fake_add(uid: str, _messages: list, **_kw) -> None:
        captured["add_uid"].append(uid)

    async def fake_draft(*_a, **_kw) -> str:
        return "Hi,\n\nConfirmed — happy to proceed."

    async def fake_plan(_email: dict, **_kw) -> list:
        return []

    with patch("acb_skills.memory_tools._set_memory_user_id", fake_set), \
            patch("acb_skills.memory_tools.remember", fake_remember), \
            patch("acb_memory.add_memories_background", fake_add), \
            patch.object(_drafting, "_llm_draft_reply", fake_draft), \
            patch.object(_drafting, "_draft_consult_plan", fake_plan):
        out = await _drafting._orchestrate_draft(
            {"from": "rep@3ds.com", "subject": "s", "body": "b"},
            about="", signature="", user_email="Me@X.com", account_id="acc-1")
        await _drain_background_tasks()

    assert out.startswith("Hi,")
    # The drafter's remember() read is steered to the account scope …
    assert captured["set_uid"] == ["me@x.com#acct:acc-1"]
    # … and the exchange it records writes back under the SAME scope.
    assert captured["add_uid"] == ["me@x.com#acct:acc-1"]


async def test_orchestrate_draft_without_account_uses_bare_email() -> None:
    # Legacy call path (no account_id) keeps the old global scope — no regression.
    captured: dict[str, list[str]] = {"set_uid": [], "add_uid": []}

    async def fake_remember(_q: str) -> str:
        return "(no relevant memories found)"

    def fake_set(uid: str) -> None:
        captured["set_uid"].append(uid)

    async def fake_add(uid: str, _messages: list, **_kw) -> None:
        captured["add_uid"].append(uid)

    async def fake_draft(*_a, **_kw) -> str:
        return "Hi."

    async def fake_plan(_email: dict, **_kw) -> list:
        return []

    with patch("acb_skills.memory_tools._set_memory_user_id", fake_set), \
            patch("acb_skills.memory_tools.remember", fake_remember), \
            patch("acb_memory.add_memories_background", fake_add), \
            patch.object(_drafting, "_llm_draft_reply", fake_draft), \
            patch.object(_drafting, "_draft_consult_plan", fake_plan):
        await _drafting._orchestrate_draft(
            {"from": "rep@3ds.com", "subject": "s", "body": "b"},
            about="", signature="", user_email="me@x.com")
        await _drain_background_tasks()

    assert captured["set_uid"] == ["me@x.com"]
    assert captured["add_uid"] == ["me@x.com"]


# ── is_default flag ──────────────────────────────────────────────────────────

def test_account_model_defaults_is_default_false() -> None:
    a = EmailAccountModel(id="a1", provider="gmail", email_address="me@x.com")
    assert a.is_default is False


def test_account_model_roundtrips_is_default() -> None:
    a = EmailAccountModel(
        id="a1", provider="gmail", email_address="me@x.com", is_default=True)
    assert a.model_dump()["is_default"] is True


def test_account_model_is_in_all_inboxes_by_default() -> None:
    """EM-T8g-1: migration 229 gives each row true, and so does the model."""
    a = EmailAccountModel(id="a1", provider="gmail", email_address="me@x.com")
    assert a.model_dump()["in_all_inboxes"] is True
    kept = EmailAccountModel(
        id="a2", provider="gmail", email_address="me@x.com", in_all_inboxes=False)
    assert kept.model_dump()["in_all_inboxes"] is False
