"""P-4a (task 3960): the 12 WhatsApp read tools of the chat.

Each tool reads one gateway route and sends nothing. The gateway is faked at
``_get``. The routes check the account owner, and their SQL has its own tests.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def wa():
    path = REPO / "apps" / "agents" / "agent-whatsapp-assistant" / "agents.py"
    spec = importlib.util.spec_from_file_location("wa_reads", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gw(monkeypatch, mod, answers: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def fake_get(path, params=None):
        seen.append((path, dict(params or {})))
        return answers[path]

    async def fake_post(*_a, **_k):  # a read tool must never post
        raise AssertionError("a read tool posted")

    monkeypatch.setattr(mod, "_get", fake_get)
    monkeypatch.setattr(mod, "_post", fake_post)
    return seen


def _run(coro):
    return asyncio.run(coro)


def test_streams(monkeypatch, wa) -> None:
    seen = _gw(monkeypatch, wa, {"/whatsapp/streams": {
        "needs_reply": 3, "waiting": 2, "groups": 4, "all": 9, "snoozed": 1}})
    out = _run(wa.whatsapp_streams("a1"))
    assert "3 need reply" in out and "1 snoozed" in out
    assert seen == [("/whatsapp/streams", {"account_id": "a1"})]


def test_pulse_clamps_days(monkeypatch, wa) -> None:
    seen = _gw(monkeypatch, wa, {"/whatsapp/pulse": {
        "window_days": 90, "inbound": 5, "outbound": 4, "active_chats": 2,
        "response": {"replied": 3, "median_minutes": 12.0, "p90_minutes": 40.0},
        "waiting_longest": [{"chat_id": "c1", "name": "Ann", "waited_hours": 30.0}],
        "by_intent": [{"key": "quote_request", "count": 2}],
        "busiest": [{"chat_id": "c2", "name": "Bo", "count": 7}]}})
    out = _run(wa.whatsapp_pulse(days=500))
    assert seen[0][1] == {"days": 90}
    assert "5 in" in out and "waiting 30.0h: Ann" in out and "busiest Bo: 7" in out


def test_labels(monkeypatch, wa) -> None:
    _gw(monkeypatch, wa, {"/whatsapp/labels": [
        {"wa_label_id": "l1", "name": "VIP", "chat_count": 4}]})
    assert "VIP — 4 chats" in _run(wa.list_whatsapp_labels())
    _gw(monkeypatch, wa, {"/whatsapp/labels": []})
    assert _run(wa.list_whatsapp_labels()) == "No WhatsApp labels."


def test_categories_use_the_first_account(monkeypatch, wa) -> None:
    seen = _gw(monkeypatch, wa, {
        "/whatsapp/accounts": [{"id": "a9"}],
        "/whatsapp/categories": [{"id": "k1", "name": "Dealers",
            "notify_policy": "instant", "auto_reply_policy": "never",
            "draft_policy": "always"}]})
    out = _run(wa.list_whatsapp_categories())
    assert "Dealers" in out and "draft=always" in out
    assert seen[-1] == ("/whatsapp/categories", {"account_id": "a9"})


def test_no_account_says_so(monkeypatch, wa) -> None:
    _gw(monkeypatch, wa, {"/whatsapp/accounts": []})
    for call in (wa.list_whatsapp_categories, wa.list_whatsapp_templates,
                 wa.list_whatsapp_saved_replies, wa.whatsapp_rules_preview,
                 wa.list_whatsapp_calls, wa.whatsapp_call_diagnostics):
        assert "No WhatsApp Business number" in _run(call())


def test_templates(monkeypatch, wa) -> None:
    seen = _gw(monkeypatch, wa, {"/whatsapp/templates": [
        {"name": "order_dispatched", "language": "en", "category": "UTILITY",
         "meta_status": "approved", "body": "Hi {{1}}"}]})
    out = _run(wa.list_whatsapp_templates("a1", approved_only=True))
    assert "order_dispatched" in out and "approved" in out
    assert seen[0][1] == {"account_id": "a1", "approved_only": True}


def test_saved_replies(monkeypatch, wa) -> None:
    _gw(monkeypatch, wa, {"/whatsapp/saved-replies": [
        {"title": "Thanks", "body": "Thank you", "shortcut": "/thx"}]})
    out = _run(wa.list_whatsapp_saved_replies("a1"))
    assert "Thanks /thx: Thank you" in out


def test_rules_preview_sends_nothing(monkeypatch, wa) -> None:
    seen = _gw(monkeypatch, wa, {"/whatsapp/rules/preview": {
        "items": [{"chat_id": "c1", "name": "Ann", "action": "draft",
                   "reason": "draft on quote_request", "requires_approval": True}],
        "summary": {"draft": 1}}})
    out = _run(wa.whatsapp_rules_preview("a1", limit=9999))
    assert seen[0][1]["limit"] == 200 and seen[0][1]["within_office_hours"] is True
    assert "nothing sent" in out and "needs approval" in out and "chat_id=c1" in out


def test_calls_and_bridge_down(monkeypatch, wa) -> None:
    _gw(monkeypatch, wa, {"/whatsapp/calls": {"bridge_reachable": True, "calls": [
        {"call_id": "x1", "direction": "in", "kind": "voice", "peer": "+91",
         "phase": "ended", "started_at": "t"}]}})
    assert "call_id=x1" in _run(wa.list_whatsapp_calls("a1"))
    _gw(monkeypatch, wa, {"/whatsapp/calls": {"bridge_reachable": False, "calls": []}})
    assert "does not answer" in _run(wa.list_whatsapp_calls("a1"))


def test_call_events(monkeypatch, wa) -> None:
    seen = _gw(monkeypatch, wa, {"/whatsapp/calls/x1/events": {
        "bridge_reachable": True, "call": {"phase": "ended", "end_reason": "timeout"},
        "events": [{"at": "t1", "kind": "offer", "detail": "sent"}]}})
    out = _run(wa.whatsapp_call_events("x1", "a1"))
    assert "ended timeout" in out and "offer: sent" in out
    assert seen[0] == ("/whatsapp/calls/x1/events", {"account_id": "a1"})


def test_call_diagnostics(monkeypatch, wa) -> None:
    _gw(monkeypatch, wa, {"/whatsapp/calls/diagnostics": {
        "bridge_reachable": True, "verdict": "Ready to call", "session_exists": True,
        "logged_in": True, "connected": True, "caller_ready": True, "active_calls": 0}})
    out = _run(wa.whatsapp_call_diagnostics("a1"))
    assert "Ready to call" in out and "caller_ready=True" in out


def test_connection_info_hides_the_verify_token(monkeypatch, wa) -> None:
    _gw(monkeypatch, wa, {"/whatsapp/connection/info": {
        "webhook_url": "https://x.io/whatsapp/webhook", "verify_token": "SECRET-TOKEN",
        "embedded_signup": False, "graph_version": "v21.0"}})
    out = _run(wa.whatsapp_connection_info())
    assert "https://x.io/whatsapp/webhook" in out and "SECRET-TOKEN" not in out


def test_link_status(monkeypatch, wa) -> None:
    _gw(monkeypatch, wa, {"/me/whatsapp-link": {
        "enabled": True, "display_number": "+91 1", "links": [
            {"organization_name": "Acme", "status": "active", "phone_hint": "1234"}]}})
    out = _run(wa.whatsapp_link_status())
    assert "Acme: active phone ending 1234" in out
    _gw(monkeypatch, wa, {"/me/whatsapp-link": {"enabled": False, "links": []}})
    assert "not set up" in _run(wa.whatsapp_link_status())


def test_the_pack_holds_the_twelve_tools(wa) -> None:
    import json

    cfg = json.loads((REPO / "apps/agents/agent-whatsapp-assistant/config.json")
                     .read_text(encoding="utf-8"))
    names = {fn.__name__ for fn in wa._TOOLS}
    new = {"whatsapp_streams", "whatsapp_pulse", "list_whatsapp_labels",
           "list_whatsapp_categories", "list_whatsapp_templates",
           "list_whatsapp_saved_replies", "whatsapp_rules_preview",
           "list_whatsapp_calls", "whatsapp_call_events",
           "whatsapp_call_diagnostics", "whatsapp_connection_info",
           "whatsapp_link_status"}
    assert new <= names and new <= set(cfg["own_tool_scope"])
