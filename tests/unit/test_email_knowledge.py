"""Unit tests for Phase 2 — drafting context (writing style / personal
instructions / knowledge base) and the knowledge-base CRUD.

DB + LLM are mocked, so no DB or network is required.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes import email as m
from tests.unit._email_fakes import bind_db


# ── Settings model carries the new drafting fields ──────────────────────────

def test_settings_defaults_include_drafting_fields() -> None:
    # OFF: LIVE drafting (DRAFT_EMAIL on the Needs Reply rule) is OFF for a new
    # mailbox until a member turns it on (D-EM-6, 2026-10-02). See
    # test_email_auto_draft_defaults.py.
    s = m.AssistantSettingsModel(account_id="acc-1")
    assert s.draft_replies is False
    assert s.personal_instructions is None
    assert s.writing_style is None


def test_settings_roundtrip_drafting_fields() -> None:
    s = m.AssistantSettingsModel(
        account_id="acc-1",
        personal_instructions="Never quote prices over email.",
        writing_style="Short and casual.",
        draft_replies=False,
    )
    d = s.model_dump()
    assert d["personal_instructions"] == "Never quote prices over email."
    assert d["writing_style"] == "Short and casual."
    assert d["draft_replies"] is False


# ── _load_assistant_about builds the tagged context block ───────────────────

async def test_load_assistant_about_builds_tagged_blocks() -> None:
    settings_row = SimpleNamespace(
        about="I run sales at Constellation.",
        signature="— Vijay",
        personal_instructions="Never quote prices.",
        writing_style="Short and casual.",
    )
    kb_rows = [SimpleNamespace(title="Pricing", content="Plans start at $10/mo.")]
    lp_rows = [SimpleNamespace(pattern="Keep sign-offs to just my first name.")]
    settings_res = MagicMock()
    settings_res.fetchone.return_value = settings_row
    kb_res = MagicMock()
    kb_res.fetchall.return_value = kb_rows
    vp_res = MagicMock()  # voice profile: none built
    vp_res.fetchone.return_value = None
    lp_res = MagicMock()
    lp_res.fetchall.return_value = lp_rows

    db = AsyncMock()
    db.execute.side_effect = [settings_res, kb_res, vp_res, lp_res]

    about, sig = await m._load_assistant_about(db, "acc-1")
    assert sig == "— Vijay"
    assert "<about>" in about and "Constellation" in about
    assert "<personal_instructions>" in about and "Never quote prices." in about
    assert "<writing_style>" in about and "Short and casual." in about
    assert "<knowledge_base>" in about and "## Pricing" in about
    assert "<learned_patterns>" in about and "sign-offs" in about


async def test_load_assistant_about_empty_when_unset() -> None:
    settings_res = MagicMock()
    settings_res.fetchone.return_value = None
    kb_res = MagicMock()
    kb_res.fetchall.return_value = []
    vp_res = MagicMock()
    vp_res.fetchone.return_value = None
    lp_res = MagicMock()
    lp_res.fetchall.return_value = []
    db = AsyncMock()
    db.execute.side_effect = [settings_res, kb_res, vp_res, lp_res]

    about, sig = await m._load_assistant_about(db, "acc-1")
    assert about == ""
    assert sig == ""


# ── Knowledge-base CRUD ─────────────────────────────────────────────────────

async def test_create_knowledge_inserts_and_returns() -> None:
    db = AsyncMock()
    user = SimpleNamespace(email="u@example.com")
    req = m.KnowledgeModel(account_id="acc-1", title="FAQ", content="Answers.")
    with patch.object(m.automation.assistant, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.assistant, "_assert_account_owner", AsyncMock()):
        res = await m.create_knowledge(req, user=user)
    assert res["title"] == "FAQ"
    assert res["account_id"] == "acc-1"
    db.commit.assert_awaited()


async def test_list_knowledge_returns_entries() -> None:
    rows = [
        SimpleNamespace(id="k1", title="Pricing", content="…", updated_at=None),
        SimpleNamespace(id="k2", title="Policy", content="…", updated_at=None),
    ]
    result = MagicMock()
    result.fetchall.return_value = rows
    db = AsyncMock()
    db.execute.return_value = result
    user = SimpleNamespace(email="u@example.com")
    with patch.object(m.automation.assistant, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.assistant, "_assert_account_owner", AsyncMock()):
        res = await m.list_knowledge(account_id="acc-1", user=user)
    assert [e["title"] for e in res["entries"]] == ["Pricing", "Policy"]
