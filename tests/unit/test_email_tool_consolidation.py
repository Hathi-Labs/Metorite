"""Consolidated email-assistant tools: the generalized tools must dispatch to
the right endpoint per their mode/kind arg, and the old behaviours must still be
reachable (so the consolidation is behaviour-preserving, just fewer tools).

Also asserts the registered tool surface shrank and no longer exposes the merged
names, while the quick-action helper functions (search_emails / find_urgent)
remain importable for the gateway's /ai/quick-action endpoint.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_AGENT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-email-assistant" / "agents.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("ea_consolidation", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()


@pytest.fixture()
def calls(monkeypatch):
    """Record every gateway call the tool makes, returning canned data."""
    rec: dict[str, list] = {"get": [], "post": [], "patch": [], "delete": []}

    async def fake_get(path, params=None):
        rec["get"].append((path, params or {}))
        if path == "/email/rules":
            return {"rules": [{"id": "r1", "name": "Bank", "enabled": True,
                               "actions": []}]}
        if path.endswith("/full-body"):
            return {"subject": "S", "from": "a@b.com", "body_text": "FULL BODY"}
        if path.startswith("/email/messages/"):
            return {"from_address": {"name": "A", "email": "a@b.com"},
                    "subject": "Hi", "body_text": "body", "to_addresses": [],
                    "cc_addresses": [], "attachments": []}
        if path == "/email/knowledge":
            return {"entries": [{"id": "k1", "title": "Old", "content": "c"}]}
        if path == "/email/priority":
            return {"emails": [{"message_id": "p1", "from": "X",
                                "subject": "Imp", "reason": "unread"}]}
        if path == "/email/reply-zero":
            return {"threads": [{"message_id": "n1", "from": "Y", "subject": "NR"}]}
        if path == "/email/messages":  # find_urgent query path
            return {"emails": [], "total": 0}
        if path == "/email/digest":
            return {"sections": [{"title": "To reply", "count": 2,
                                  "items": [{"subject": "One"}, {"subject": "Two"}]}]}
        if path == "/email/learned-patterns":
            return {"patterns": [{"id": "lp1", "pattern": "short replies"}]}
        if path == "/email/rules/patterns":
            return {"patterns": [{"id": "rp1", "pattern_type": "from",
                                  "value": "x", "rule_name": "R"}]}
        return {}

    async def fake_post(path, body):
        rec["post"].append((path, body))
        if path == "/email/sync":
            return {}
        if "resync" in path:
            return {"messages_synced": 42}
        if path.endswith("/approve"):
            return {"actions": ["ARCHIVE"]}
        if path.endswith("/undo"):
            return {"reversed": ["ARCHIVE"]}
        if path == "/email/digest/send":
            return {"to": "me@x.com"}
        return {"id": "new1"}

    async def fake_patch(path, body):
        rec["patch"].append((path, body))
        return {}

    async def fake_delete(path):
        rec["delete"].append(path)
        return {}

    monkeypatch.setattr(agents, "_get", fake_get)
    monkeypatch.setattr(agents, "_post", fake_post)
    monkeypatch.setattr(agents, "_patch", fake_patch)
    monkeypatch.setattr(agents, "_delete", fake_delete)
    return rec


# ── Registered surface ───────────────────────────────────────────────────────

def test_surface_shrank_and_merged_names_gone() -> None:
    tools = agents._register_agent_tools()
    # 41 after the consolidation pass, +1 for auto_categorize_inbox (the
    # uncategorized-inbox sweep — categorize_senders only re-projects existing
    # rule labels and cannot categorize mail the rules never reached).
    assert len(tools) == 42
    for gone in ("search_emails", "get_important_emails", "find_urgent",
                 "find_needs_reply", "get_full_body_email", "update_rule_state",
                 "approve_execution", "reject_execution", "undo_execution",
                 "get_digest", "send_digest", "resync_account", "add_knowledge",
                 "update_knowledge", "list_learned_patterns", "list_rule_patterns",
                 "delete_learned_pattern", "delete_rule_pattern",
                 # aggressive pass
                 "send_reply", "get_sender_categories", "suggest_unsubscribes",
                 "list_cold_senders", "keep_newsletter", "set_cold_sender",
                 # final pass
                 "get_unread_count", "move_to_folder", "reset_rules",
                 "run_rules_now", "process_past_emails",
                 # cleanup pass
                 "apply_labels", "import_artifact"):
        assert gone not in tools
    for new in ("find_priority", "resolve_execution", "digest", "save_knowledge",
                "list_patterns", "forget_pattern", "set_sender_status", "run_rules"):
        assert new in tools
    # Quick-action helpers stay importable even though they're unregistered.
    assert callable(agents.search_emails) and callable(agents.find_urgent)
    assert callable(agents.suggest_unsubscribes)  # /ai/quick-action=unsubscribe


# ── run_rules(scope) + install_default_rules(reset) + manage_inbox(move) ──────

async def test_run_rules_scope_dispatch(calls) -> None:
    await agents.run_rules("acc", scope="new")
    assert calls["post"][-1][0] == "/email/rules/run"
    await agents.run_rules("acc", scope="past", days=14)
    assert calls["post"][-1][0] == "/email/rules/process-past"


async def test_install_default_rules_reset(calls) -> None:
    await agents.install_default_rules("acc")
    assert "install-presets" in calls["post"][-1][0]
    await agents.install_default_rules("acc", reset=True)
    assert "reset" in calls["post"][-1][0]


async def test_manage_inbox_move_uses_patch(calls) -> None:
    # No account_id: each message acts in its own mailbox (EM-T8e-2).
    await agents.manage_inbox("archive", ["m1"])
    assert calls["post"][-1][0] == "/email/messages/bulk"
    out = await agents.manage_inbox("move", ["m1", "m2"], folder="Archive")
    # move goes through per-message PATCH, not the bulk endpoint.
    assert any(p[0] == "/email/messages/m1" for p in calls["patch"])
    assert "Moved" in out
    # move without a folder is rejected.
    assert "folder" in await agents.manage_inbox("move", ["m1"])


async def test_manage_inbox_label_absorbs_apply_labels(calls) -> None:
    out = await agents.manage_inbox(
        "label", ["m1", "m2"], add_labels=["Finance"], remove_labels=["FYI"])
    # label goes through per-message PATCH carrying add/remove labels.
    patched = [p for p in calls["patch"] if p[0] == "/email/messages/m1"]
    assert patched and patched[-1][1].get("add_labels") == ["Finance"]
    assert patched[-1][1].get("remove_labels") == ["FYI"]
    assert "Updated labels" in out
    # label with nothing to change is rejected.
    assert "add_labels" in await agents.manage_inbox("label", ["m1"])


# ── send_email absorbing send_reply ──────────────────────────────────────────

async def test_send_email_reply_derives_recipient_and_subject(calls, monkeypatch) -> None:
    async def yes(**kw):
        return True
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", yes)
    out = await agents.send_email("acc", body="Thanks!", reply_to_email_id="m1")
    # Derived to/subject from the original (fake_get returns from a@b.com / "Hi").
    sent = [c for c in calls["post"] if c[0] == "/email/send"][-1][1]
    assert sent["to"] == ["a@b.com"]
    assert sent["subject"] == "Re: Hi"
    assert sent["reply_to_message_id"] == "m1"
    assert "Replied to" in out


async def test_send_email_new_requires_recipient(calls, monkeypatch) -> None:
    async def yes(**kw):
        return True
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", yes)
    out = await agents.send_email("acc", body="Hi", subject="S")
    assert "No recipient" in out  # no `to` and no reply target


# ── list_senders(view) + set_sender_status ───────────────────────────────────

async def test_list_senders_view_dispatch(calls) -> None:
    await agents.list_senders("acc", view="top")
    assert calls["get"][-1][0] == "/email/senders"
    await agents.list_senders("acc", view="categories")
    assert calls["get"][-1][0] == "/email/senders/categories"
    await agents.list_senders("acc", view="cold")
    assert calls["get"][-1][0] == "/email/cold-senders"
    await agents.list_senders("acc", view="unsubscribe")
    assert calls["get"][-1][0] == "/email/senders"  # suggest_unsubscribes path


async def test_set_sender_status_dispatch(calls) -> None:
    await agents.set_sender_status("acc", "x@y.com", "cold")
    assert calls["post"][-1][0] == "/email/cold-senders"
    assert calls["post"][-1][1]["status"] == "AI_LABELED_COLD"
    await agents.set_sender_status("acc", "x@y.com", "not_cold")
    assert calls["post"][-1][1]["status"] == "USER_REJECTED_COLD"
    await agents.set_sender_status("acc", "x@y.com", "keep")
    assert calls["post"][-1][0] == "/email/newsletters"
    bad = await agents.set_sender_status("acc", "x@y.com", "weird")
    assert "cold" in bad


# ── find_priority ────────────────────────────────────────────────────────────

async def test_find_priority_dispatch(calls) -> None:
    await agents.find_priority("acc", kind="important")
    assert calls["get"][-1][0] == "/email/priority"
    await agents.find_priority("acc", kind="needs_reply")
    assert calls["get"][-1][0] == "/email/reply-zero"
    await agents.find_priority("acc", kind="urgent")
    assert calls["get"][-1][0] == "/email/messages"
    # Unknown kind falls back to needs-reply.
    await agents.find_priority("acc", kind="weird")
    assert calls["get"][-1][0] == "/email/reply-zero"


# ── read_email(full=…) ───────────────────────────────────────────────────────

async def test_read_email_full_hits_fullbody(calls) -> None:
    out = await agents.read_email("m1", full=True)
    assert calls["get"][-1][0] == "/email/messages/m1/full-body"
    assert "FULL BODY" in out
    await agents.read_email("m1")
    assert calls["get"][-1][0] == "/email/messages/m1"


# ── update_rule(enabled=…) ───────────────────────────────────────────────────

async def test_update_rule_toggle(calls) -> None:
    out = await agents.update_rule("acc", "r1", enabled=False)
    assert calls["patch"][-1][1]["enabled"] is False
    assert "disabled" in out


# ── resolve_execution ────────────────────────────────────────────────────────

async def test_resolve_execution_decisions(calls) -> None:
    await agents.resolve_execution("e1", "approve")
    assert calls["post"][-1][0].endswith("/approve")
    await agents.resolve_execution("e1", "undo")
    assert calls["post"][-1][0].endswith("/undo")
    bad = await agents.resolve_execution("e1", "nope")
    assert "approve" in bad


# ── digest ───────────────────────────────────────────────────────────────────

async def test_digest_preview_is_formatted_not_json(calls, monkeypatch) -> None:
    async def yes(**kw):
        return True
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", yes)
    out = await agents.digest("acc", period="day")
    assert calls["get"][-1][0] == "/email/digest"
    assert "To reply" in out and "{" not in out  # readable bullets, not raw JSON
    sent = await agents.digest("acc", send=True)
    assert calls["post"][-1][0] == "/email/digest/send"
    assert "sent" in sent


async def test_digest_send_is_gated_and_fails_closed(calls, monkeypatch) -> None:
    """send=True emails the mailbox — a confirmation card the user must approve.
    With no interactive stream (non-interactive default deny) it must NOT send."""
    async def no(**kw):
        return False
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", no)
    out = await agents.digest("acc", send=True)
    assert "Cancelled" in out
    assert not any(c[0] == "/email/digest/send" for c in calls["post"])


# ── sync_account ─────────────────────────────────────────────────────────────

async def test_sync_account_incremental_vs_full(calls, monkeypatch) -> None:
    async def yes(**kw):
        return True
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", yes)
    await agents.sync_account("acc")
    assert calls["post"][-1][0] == "/email/sync"
    await agents.sync_account("acc", full=True)
    assert "resync" in calls["post"][-1][0]
    await agents.sync_account("acc", purge=True)
    assert "purge=true" in calls["post"][-1][0]


async def test_sync_purge_is_gated_and_fails_closed(calls, monkeypatch) -> None:
    """purge deletes local mail first — confirm, fail-closed. A declined (or
    non-interactive) confirmation must not purge or resync."""
    async def no(**kw):
        return False
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", no)
    out = await agents.sync_account("acc", purge=True)
    assert "Cancelled" in out
    assert not any("resync" in c[0] for c in calls["post"])


# ── save_knowledge ───────────────────────────────────────────────────────────

async def test_save_knowledge_add_vs_update(calls) -> None:
    # title and content are keyword-only since EM-T8e-2 made account_id
    # optional, so the tool schema still marks both as required.
    await agents.save_knowledge("acc", title="T", content="C")  # add → POST
    assert calls["post"][-1][0] == "/email/knowledge"
    await agents.save_knowledge(
        "acc", title="T2", content="C2", knowledge_id="k1")  # edit → PATCH
    assert calls["patch"][-1][0] == "/email/knowledge/k1"


# ── list_patterns / forget_pattern ───────────────────────────────────────────

async def test_patterns_by_kind(calls) -> None:
    await agents.list_patterns("acc", kind="draft")
    assert calls["get"][-1][0] == "/email/learned-patterns"
    await agents.list_patterns("acc", kind="rule")
    assert calls["get"][-1][0] == "/email/rules/patterns"
    await agents.forget_pattern("lp1", kind="draft")
    assert calls["delete"][-1] == "/email/learned-patterns/lp1"
    await agents.forget_pattern("rp1", kind="rule")
    assert calls["delete"][-1] == "/email/rules/patterns/rp1"
