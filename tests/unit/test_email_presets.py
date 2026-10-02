"""Unit tests for the default-rule preset installer (used by the UI's
'Add defaults' and the AI assistant's install_default_rules tool)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.routes import email as m
from tests.unit._email_fakes import bind_db

_actions_for_preset = m.automation.rules._actions_for_preset


def test_preset_set_matches_inbox_zero_system_rules() -> None:
    names = [p["name"] for p in m._PRESET_RULES]
    assert names == [
        "Needs Reply", "Awaiting Reply", "Done", "FYI", "Newsletter",
        "Marketing", "Calendar", "Receipt", "Notification", "Cold Email",
    ]
    # FYI/Needs Reply run on threads.
    reply = next(p for p in m._PRESET_RULES if p["name"] == "Needs Reply")
    assert reply["run_on_threads"] is True


# ── D-EM-6: reply drafting is OFF for a new mailbox ─────────────────────────
# The presets used to carry DRAFT_EMAIL on Needs Reply, so 'Add defaults' and
# 'Reset rules' turned drafting on whatever the switch said. Now the seed adds
# it only when the account's stored "Auto draft replies" is true. Spec:
# email_app_master_plan.md §10.4 EM-T7.


def _types(acts) -> list[str]:
    return [getattr(a, "type", None) or a["type"] for a in acts]


def test_no_preset_drafts_by_default() -> None:
    for provider in ("gmail", "microsoft"):
        for p in m._PRESET_RULES:
            assert "DRAFT_EMAIL" not in _types(_actions_for_preset(p, provider)), (
                p["name"], provider)


def test_only_needs_reply_drafts_when_the_member_turned_it_on() -> None:
    for provider in ("gmail", "microsoft"):
        drafting = [
            p["name"] for p in m._PRESET_RULES
            if "DRAFT_EMAIL" in _types(
                _actions_for_preset(p, provider, draft_replies=True))
        ]
        assert drafting == ["Needs Reply"], provider


async def _seed_through(handler, *, stored_draft_replies: bool | None) -> dict:
    """Run a REAL preset handler. ``stored_draft_replies=None`` means the
    mailbox has no settings row. Returns {rule name: [action types]}."""
    row = SimpleNamespace(provider="gmail")
    if stored_draft_replies is not None:
        row.draft_replies = stored_draft_replies
    rule_names: dict[str, str] = {}
    by_rule: dict[str, list[str]] = {}

    def _execute(sql, params=None):
        sql = str(sql)
        if params and "INSERT INTO email_rules" in sql:
            rule_names[params["id"]] = params["name"]
        no_row = "FROM email_assistant_settings" in sql and stored_draft_replies is None
        return MagicMock(fetchone=MagicMock(return_value=None if no_row else row),
                         fetchall=MagicMock(return_value=[]))

    async def _replace(_db, rid, acts):
        by_rule[rule_names[rid]] = _types(acts)

    db = AsyncMock()
    db.execute.side_effect = _execute
    user = SimpleNamespace(email="u@example.com")
    with patch.object(m.automation.rules, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.rules, "_assert_account_owner", AsyncMock()), \
            patch.object(m.automation.rules, "_load_rules", AsyncMock(return_value=[])), \
            patch.object(m.automation.rules, "_replace_actions",
                         AsyncMock(side_effect=_replace)):
        await handler(account_id="acc-1", user=user)
    return by_rule


async def test_add_defaults_on_a_new_mailbox_creates_no_draft_action() -> None:
    by_rule = await _seed_through(m.install_preset_rules, stored_draft_replies=None)
    assert "Needs Reply" in by_rule
    assert all("DRAFT_EMAIL" not in t for t in by_rule.values()), by_rule


async def test_reset_on_a_new_mailbox_creates_no_draft_action() -> None:
    by_rule = await _seed_through(m.reset_rules, stored_draft_replies=None)
    assert len(by_rule) == len(m._PRESET_RULES)
    assert all("DRAFT_EMAIL" not in t for t in by_rule.values()), by_rule


async def test_add_defaults_with_drafting_stored_off_creates_no_draft_action() -> None:
    by_rule = await _seed_through(m.install_preset_rules, stored_draft_replies=False)
    assert all("DRAFT_EMAIL" not in t for t in by_rule.values()), by_rule


async def test_add_defaults_after_the_member_turned_drafting_on() -> None:
    """The feature stays: a member who turned the switch on gets a Needs Reply
    rule that drafts, and no other rule drafts."""
    by_rule = await _seed_through(m.install_preset_rules, stored_draft_replies=True)
    drafting = [name for name, t in by_rule.items() if "DRAFT_EMAIL" in t]
    assert drafting == ["Needs Reply"]


# Names that become FOLDER moves on Outlook (inbox-zero parity) vs stay as
# category LABELs.
_MS_FOLDER_NAMES = {"Newsletter", "Marketing", "Receipt", "Notification", "Cold Email"}
# Marketing & Cold Email label_archive on Gmail; on Outlook the folder move files
# them (no archive — archiving would re-file them out of their folder).
_GMAIL_ARCHIVE_NAMES = {"Marketing", "Cold Email"}


def test_actions_for_preset_outlook_labels_and_files_cleanup_categories() -> None:
    for p in m._PRESET_RULES:
        types = [a["type"] for a in _actions_for_preset(p, "microsoft")]
        if p["name"] in _MS_FOLDER_NAMES:
            # Outlook tags the category (LABEL) AND files it into the folder.
            assert types[0] == "LABEL", p["name"]
            assert "MOVE_FOLDER" in types, p["name"]
            # The folder move files the mail; never pair it with ARCHIVE (that
            # would move it straight back out into the Archive folder).
            assert "ARCHIVE" not in types, p["name"]
        else:
            assert types[0] == "LABEL", p["name"]
            assert "MOVE_FOLDER" not in types, p["name"]


def test_actions_for_preset_gmail_is_label_only() -> None:
    for p in m._PRESET_RULES:
        types = [a["type"] for a in _actions_for_preset(p, "gmail")]
        assert "MOVE_FOLDER" not in types, p["name"]
        assert types[0] == "LABEL", p["name"]
    # Marketing & Cold Email still archive on Gmail.
    for name in _GMAIL_ARCHIVE_NAMES:
        p = next(x for x in m._PRESET_RULES if x["name"] == name)
        assert "ARCHIVE" in [a["type"] for a in _actions_for_preset(p, "gmail")]


async def test_install_presets_uses_folder_actions_for_outlook() -> None:
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchone=MagicMock(return_value=SimpleNamespace(provider="microsoft"))
    )
    user = SimpleNamespace(email="u@example.com")
    captured: list = []
    with patch.object(m.automation.rules, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.rules, "_assert_account_owner", AsyncMock()), \
            patch.object(m.automation.rules, "_load_rules", AsyncMock(return_value=[])), \
            patch.object(m.automation.rules, "_replace_actions",
                         AsyncMock(side_effect=lambda _db, _rid, acts: captured.append(acts))):
        await m.install_preset_rules(account_id="acc-1", user=user)
    all_types = {a.type for acts in captured for a in acts}
    assert "MOVE_FOLDER" in all_types  # Outlook files promo mail into folders


def _db_with_provider(provider: str = "gmail", patterns=None) -> AsyncMock:
    """AsyncMock db whose provider-lookup SELECT returns ``provider``.

    ``patterns`` is what the learned-pattern snapshot SELECT returns (reset
    carries these across the reseed — see test_reset_preserves_learned_patterns).
    """
    db = AsyncMock()
    db.execute.return_value = MagicMock(
        fetchone=MagicMock(return_value=SimpleNamespace(provider=provider)),
        fetchall=MagicMock(return_value=patterns or []),
    )
    return db


async def test_install_presets_creates_only_missing() -> None:
    # The existing rule carries the LEGACY name ("Reply", pre-mig-92): the
    # renamed "Needs Reply" preset must still be skipped, not installed beside
    # it — two conversation rules for one status would double-classify.
    db = _db_with_provider()
    user = SimpleNamespace(email="u@example.com")
    with patch.object(m.automation.rules, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.rules, "_assert_account_owner", AsyncMock()), \
            patch.object(m.automation.rules, "_load_rules",
                         AsyncMock(return_value=[{"name": "Reply"}])), \
            patch.object(m.automation.rules, "_replace_actions", AsyncMock()):
        res = await m.install_preset_rules(account_id="acc-1", user=user)
    assert "Needs Reply" not in res["installed"]   # legacy alias → skipped
    assert "FYI" in res["installed"]
    assert len(res["installed"]) == 9
    assert res["total_presets"] == 10
    db.commit.assert_awaited()


async def test_install_presets_idempotent_when_all_present() -> None:
    db = _db_with_provider()
    user = SimpleNamespace(email="u@example.com")
    all_rules = [{"name": p["name"]} for p in m._PRESET_RULES]
    with patch.object(m.automation.rules, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.rules, "_assert_account_owner", AsyncMock()), \
            patch.object(m.automation.rules, "_load_rules", AsyncMock(return_value=all_rules)), \
            patch.object(m.automation.rules, "_replace_actions", AsyncMock()):
        res = await m.install_preset_rules(account_id="acc-1", user=user)
    assert res["installed"] == []


async def test_reset_rules_deletes_then_reinstalls_every_preset() -> None:
    """Reset wipes existing rules and reseeds the full preset set regardless of
    what was there before (unlike install-presets, which is additive)."""
    db = _db_with_provider("microsoft")
    user = SimpleNamespace(email="u@example.com")
    existing = [{"name": p["name"]} for p in m._PRESET_RULES]  # all present…
    with patch.object(m.automation.rules, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.rules, "_assert_account_owner", AsyncMock()), \
            patch.object(m.automation.rules, "_load_rules", AsyncMock(return_value=existing)), \
            patch.object(m.automation.rules, "_replace_actions", AsyncMock()):
        res = await m.reset_rules(account_id="acc-1", user=user)
    # …yet every preset is reinstalled, and the response flags the reset.
    assert len(res["installed"]) == len(m._PRESET_RULES)
    assert res["total_presets"] == len(m._PRESET_RULES)
    assert res["reset"] is True
    # A DELETE of the account's rules ran before reseeding.
    sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list if c.args)
    assert "DELETE FROM email_rules" in sql
    db.commit.assert_awaited()


async def test_reset_preserves_learned_patterns_across_the_reseed() -> None:
    """Reset must not destroy the user's training.

    email_rule_patterns.rule_id is ON DELETE CASCADE, so wiping the rules used to
    take every Fix / auto-learned / label-synced pattern with it — months of
    corrections gone behind a dialog that only mentioned rules. The reseed mints
    fresh UUIDs, so patterns are carried across by rule NAME and re-pointed.
    """
    saved = [
        SimpleNamespace(rule_name="Newsletter", pattern_type="FROM",
                        value="news@brand.com", exclude=False, source="FIX",
                        reason="user fix", approved_at="2026-07-01",
                        rejected_at=None),
        SimpleNamespace(rule_name="Receipt", pattern_type="SUBJECT",
                        value="your order", exclude=True, source="FIX",
                        reason=None, approved_at=None, rejected_at=None),
        # Belonged to a custom rule the reset removes — legitimately dropped.
        SimpleNamespace(rule_name="My Custom Rule", pattern_type="FROM",
                        value="x@y.com", exclude=False, source="FIX",
                        reason=None, approved_at=None, rejected_at=None),
    ]
    db = _db_with_provider("gmail", patterns=saved)
    user = SimpleNamespace(email="u@example.com")
    reseeded = [{"id": f"new-{p['name']}", "name": p["name"]}
                for p in m._PRESET_RULES]
    with patch.object(m.automation.rules, "_tenant_session", bind_db(db)), \
            patch.object(m.automation.rules, "_assert_account_owner", AsyncMock()), \
            patch.object(m.automation.rules, "_load_rules",
                         AsyncMock(return_value=reseeded)), \
            patch.object(m.automation.rules, "_replace_actions", AsyncMock()):
        res = await m.reset_rules(account_id="acc-1", user=user)

    # The two preset-attached patterns were restored; the custom-rule one wasn't.
    assert res["patterns_restored"] == 2
    inserts = [
        c.args[1] for c in db.execute.call_args_list
        if len(c.args) > 1 and "INSERT INTO email_rule_patterns" in str(c.args[0])
    ]
    assert {(i["rid"], i["val"], i["excl"]) for i in inserts} == {
        ("new-Newsletter", "news@brand.com", False),
        ("new-Receipt", "your order", True),
    }
    # Review state rides along. Re-approving is a decision the user already
    # made; dropping it here would silently retire every confirmed pattern and
    # take the Email Cleaner's strongest evidence down with it (migration 85).
    by_val = {i["val"]: i for i in inserts}
    assert by_val["news@brand.com"]["approved"] == "2026-07-01"
