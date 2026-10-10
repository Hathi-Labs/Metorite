"""D-EM-61: no member chooses the tier of an email AI task. EM-T15.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.16 and D-EM-61.

The owner, 2026-10-09: "It is hard-coded depending on our best process for
email." ``EMAIL_TASK_TIERS`` in ``automation/assistant.py`` holds the tier of
the three tasks that a member could once change: background drafts, the
composer's "Draft with AI", and the email chat. No call site reads the stored
``draft_model``, ``compose_model`` or ``chat_model``. The columns and the
request fields stay for one release (R6). The PUT ignores the fields and logs
once, and a later PR removes them (HANDOFF).

Hermetic. Each behaviour test hands the route a session that answers EVERY
query with a stored row that names the other tier. A route that read the
stored choice would send that tier, so the test fails. R8 for the PUT and
GET SQL is ``test_email_assistant_settings.py``, and R8 for the chat is
``test_email_chat_context_owner.py``.

Mutations this file catches (R7):

* the rule draft reads ``draft_model`` again -> ``test_a_rule_draft_*``;
* "Draft with AI" reads ``compose_model`` again -> ``test_draft_reply_*``
  and ``test_compose_assist_*``;
* a call site of the email routes names a retired field again
  -> ``test_no_email_route_names_a_retired_field``;
* the agent tool takes a tier argument again
  -> ``test_the_agent_tool_has_no_tier_argument``;
* the PUT stops logging, or logs on every request
  -> ``test_a_retired_field_logs_once``.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("fastapi")

from gateway.routes.email.automation import actions as actions_mod
from gateway.routes.email.automation import assistant as assistant_mod
from gateway.routes.email.automation import drafting as drafting_mod
from gateway.routes.email.automation import followups as followups_mod

from tests.unit._email_fakes import bind_db

_ROOT = Path(__file__).resolve().parents[2]
_EMAIL_ROUTES = _ROOT / "apps/services/gateway/gateway/routes/email"
_RETIRED = ("draft_model", "compose_model", "chat_model")

# Each stored value is the OTHER tier, so a read shows in the call.
_STORED = SimpleNamespace(
    draft_model="tier-fast", compose_model="tier-powerful",
    chat_model="tier-fast", rule_model="tier-powerful",
    draft_confidence="ALL_EMAILS", sensitive_data_protection=False)


class _StoredTiersDb:
    """A session that answers every query with the stored row, and keeps
    the SQL it was handed."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.sql.append(str(stmt))
        return SimpleNamespace(
            fetchone=lambda: _STORED, fetchall=lambda: [_STORED],
            scalar=lambda: None, first=lambda: _STORED)


def _assert_no_retired_read(db: _StoredTiersDb) -> None:
    for sql in db.sql:
        for name in _RETIRED:
            assert name not in sql, f"a query read {name}: {sql[:120]}"


# ── the tier table ─────────────────────────────────────────────────────────


def test_the_tier_table() -> None:
    """The best process, written once. A change here is a product decision."""
    assert dict(assistant_mod.EMAIL_TASK_TIERS) == {
        "draft": "tier-powerful", "compose": "tier-fast", "chat": "tier-powerful"}
    with pytest.raises(TypeError):
        assistant_mod.EMAIL_TASK_TIERS["draft"] = "tier-fast"  # type: ignore[index]


# ── each call site ignores a stored tier ───────────────────────────────────


async def test_draft_reply_ignores_a_stored_tier(monkeypatch) -> None:
    db = _StoredTiersDb()
    seen: dict[str, Any] = {}

    async def _draft(email, *a, **kw):
        seen["model"] = kw.get("model")
        return ""  # the confidence gate returns at once

    monkeypatch.setattr(drafting_mod, "_tenant_session", bind_db(db))
    monkeypatch.setattr(drafting_mod, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(drafting_mod, "_load_assistant_about",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr(drafting_mod, "_build_reply_context", AsyncMock(
        return_value={"subject": "s", "body": "b", "thread_id": "t1"}))
    monkeypatch.setattr(drafting_mod, "_agent_draft_reply", _draft)
    await drafting_mod.draft_reply_smart(
        drafting_mod.DraftReplyRequest(account_id="acc-1", message_id="m1"),
        SimpleNamespace(email="me@example.com"))
    assert seen["model"] == "tier-fast", (
        "the stored compose tier changed the tier of Draft with AI")
    _assert_no_retired_read(db)


@pytest.mark.parametrize("mode", ["new", "reply"])
async def test_compose_assist_ignores_a_stored_tier(monkeypatch, mode) -> None:
    db = _StoredTiersDb()
    seen: dict[str, Any] = {}

    async def _capture(*a, **kw):
        seen["model"] = kw.get("model")
        return ""

    monkeypatch.setattr(drafting_mod, "_tenant_session", bind_db(db))
    monkeypatch.setattr(drafting_mod, "_assert_account_owner", AsyncMock())
    monkeypatch.setattr(drafting_mod, "_load_assistant_about",
                        AsyncMock(return_value=("", "")))
    monkeypatch.setattr(drafting_mod, "_build_reply_context", AsyncMock(
        return_value={"subject": "s", "body": "b", "thread_id": "t1"}))
    monkeypatch.setattr(drafting_mod, "resolve_self", AsyncMock(
        return_value=SimpleNamespace(address="me@example.com", label="")))
    monkeypatch.setattr(drafting_mod, "_agent_draft_reply", _capture)
    monkeypatch.setattr(drafting_mod, "_llm_compose_assist", _capture)
    req = drafting_mod.ComposeAssistRequest(
        account_id="acc-1", mode=mode,
        message_id="m1" if mode == "reply" else None)
    await drafting_mod.compose_assist(req, SimpleNamespace(email="me@example.com"))
    assert seen["model"] == "tier-fast", mode
    _assert_no_retired_read(db)


async def test_a_rule_draft_ignores_a_stored_tier(monkeypatch) -> None:
    db = _StoredTiersDb()
    seen: dict[str, Any] = {}

    async def _draft(email, *a, **kw):
        seen["model"] = kw.get("model")
        return ""  # the confidence gate skips the draft

    monkeypatch.setattr(actions_mod, "_skip_for_paired_mailbox",
                        AsyncMock(return_value=False))
    monkeypatch.setattr(actions_mod, "_build_reply_context", AsyncMock(
        return_value={"subject": "s", "body": "b"}))
    monkeypatch.setattr(actions_mod, "_agent_draft_reply", _draft)
    await actions_mod._apply_rule_actions(
        db, None, "m1", "p1", [{"type": "DRAFT_EMAIL"}],
        email={"from": "sender@example.com", "subject": "s", "body": "hello"},
        account_id="acc-1")
    assert seen["model"] == "tier-powerful", (
        "the stored draft tier changed the tier of a rule draft")
    _assert_no_retired_read(db)


def test_the_follow_up_nudge_uses_the_draft_tier() -> None:
    """The nudge path needs a provider and a mailbox to run, so this one
    reads the source. The scan below covers the SQL."""
    src = inspect.getsource(followups_mod._maybe_send_follow_up_reminders)
    assert 'fu_model = EMAIL_TASK_TIERS["draft"]' in src


def test_no_email_route_names_a_retired_field() -> None:
    """Only assistant.py may name the three fields: the request model that
    accepts them for one release, and the tuple that logs them."""
    hits = []
    for path in sorted(_EMAIL_ROUTES.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "_account_models" in src:
            hits.append(f"{path.name}: _account_models")
        if path.name == "assistant.py":
            continue
        hits += [f"{path.name}: {n}" for n in _RETIRED if n in src]
    assert hits == []
    for fn in (assistant_mod.get_assistant_settings,
               assistant_mod.put_assistant_settings):
        src = inspect.getsource(fn)
        for name in _RETIRED:
            assert name not in src, (fn.__name__, name)


# ── the agent tool ─────────────────────────────────────────────────────────


def test_the_agent_tool_has_no_tier_argument() -> None:
    src = (_ROOT / "apps/agents/agent-email-assistant/agents.py").read_text(
        encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef)
              and n.name == "update_assistant_settings")
    params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    assert not {p for p in params if "model" in p or "tier" in p}, params
    body = ast.get_source_segment(src, fn) or ""
    for name in (*_RETIRED, "rule_model", "tier-fast", "tier-balanced",
                 "tier-powerful"):
        assert name not in body, name


# ── the request fields: accepted, ignored, logged once ─────────────────────


def test_a_retired_field_logs_once(monkeypatch) -> None:
    lines: list[tuple[str, dict]] = []
    monkeypatch.setattr(assistant_mod, "_log", SimpleNamespace(
        info=lambda event, **kw: lines.append((event, kw))))
    monkeypatch.setattr(assistant_mod, "_retired_fields_logged", set())
    model = assistant_mod.AssistantSettingsModel
    assistant_mod._log_retired_model_fields(model(account_id="a"))
    assert lines == [], "a body with no retired field logged"
    old = model(account_id="a", draft_model="tier-fast", chat_model="tier-fast")
    assistant_mod._log_retired_model_fields(old)
    assistant_mod._log_retired_model_fields(old)
    assistant_mod._log_retired_model_fields(
        model(account_id="a", compose_model="tier-powerful"))
    assert lines == [
        ("email.assistant_settings.model_field_ignored", {"field": "draft_model"}),
        ("email.assistant_settings.model_field_ignored", {"field": "chat_model"}),
        ("email.assistant_settings.model_field_ignored", {"field": "compose_model"}),
    ]
