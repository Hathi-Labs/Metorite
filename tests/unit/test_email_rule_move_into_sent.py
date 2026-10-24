"""H-395: a mail rule cannot move mail into Sent or Drafts.

#1237 refuses that move for every provider, because a held chat could use it
to fake a ``sent`` contact. A rule saved before then fails at run time.

R7 - the mutation that turns each rule red:

1. A new rule cannot hold the move (create, update, batch, generate).
   Mutation: drop ``refuse_move_into_sent`` from a route ->
   ``test_every_rule_save_refuses_the_move``.
2. An old rule's run skips the action with one plain sentence.
   Mutation: drop the ``move_refusal`` check of ``_move_folder_action`` ->
   ``test_the_run_skips_the_move_with_a_plain_sentence``.
3. Other folders still work. -> ``test_other_folders_are_allowed``.

The query for the Check of H-395 (run it on the database, not from here)::

    SELECT r.id, r.account_id, r.name, a.label
      FROM email_rules r JOIN email_actions a ON a.rule_id = r.id
     WHERE UPPER(a.type) = 'MOVE_FOLDER'
       AND LOWER(BTRIM(a.label)) IN ('sent', 'sentitems', 'sent items',
           'sent mail', 'drafts', 'draft');
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from acb_auth.roles import UserContext, UserRole
from email_ingestion.providers.base import MOVE_REFUSED_SENTENCE
from fastapi import HTTPException
from gateway.routes import email as m

_rules = m.automation.rules
_actions = m.automation.actions


def _req(label: str):
    return _rules.RuleModel(
        account_id="acc-1", name="Move",
        actions=[_rules.RuleActionModel(type="MOVE_FOLDER", label=label)])


@pytest.mark.parametrize(
    "label", ["Sent", "sent items", "Sent Mail", "Drafts", " draft "])
def test_the_refusal_names_sent_and_drafts_in_every_spelling(label: str) -> None:
    with pytest.raises(HTTPException) as info:
        _rules.refuse_move_into_sent(_req(label).actions)
    assert info.value.status_code == 422
    assert info.value.detail == MOVE_REFUSED_SENTENCE


def test_other_folders_are_allowed() -> None:
    for label in ("Archive", "Receipts", "Junk", None):
        _rules.refuse_move_into_sent(
            [_rules.RuleActionModel(type="MOVE_FOLDER", label=label)])
    # Only MOVE_FOLDER is checked: a LABEL named Sent is a label.
    _rules.refuse_move_into_sent(
        [_rules.RuleActionModel(type="LABEL", label="Sent")])


async def test_every_rule_save_refuses_the_move(monkeypatch) -> None:
    user = UserContext(email="a@b.com", role=UserRole.EXECUTIVE)

    def no_db():
        raise AssertionError("the database was opened before the refusal")

    monkeypatch.setattr(_rules, "_tenant_session", no_db)
    with pytest.raises(HTTPException) as c:
        await _rules.create_rule(_req("Sent"), user)
    assert c.value.status_code == 422
    with pytest.raises(HTTPException) as u:
        await _rules.update_rule("r1", _req("Drafts"), user)
    assert u.value.status_code == 422
    with pytest.raises(HTTPException) as b:
        await _rules._insert_specs(
            MagicMock(), "acc-1",
            [{"name": "x",
              "actions": [{"type": "MOVE_FOLDER", "label": "Sent"}]}])
    assert b.value.detail == MOVE_REFUSED_SENTENCE


async def test_the_run_skips_the_move_with_a_plain_sentence() -> None:
    provider = MagicMock()
    provider.move_to_folder = AsyncMock()
    db = MagicMock()
    db.execute = AsyncMock()
    errors: list[dict[str, str]] = []
    done = await _actions._apply_rule_actions(
        db, provider, "m1", "pm1",
        [{"type": "MOVE_FOLDER", "label": "Sent Items"}],
        {"from": "x@y.com", "subject": "s", "body": "b"}, "", "", "a@b.com",
        account_id="acc-1", errors_out=errors, rule_id="r1")
    assert done == []
    provider.move_to_folder.assert_not_called()
    assert errors == [
        {"type": "MOVE_FOLDER", "error": MOVE_REFUSED_SENTENCE[:160]}]


@pytest.mark.parametrize("label", [
    "[Gmail]/Sent Mail", "INBOX.Sent", "Sent Messages", "INBOX.Drafts",
    "[Gmail]/Drafts", "INBOX/Sent"])
def test_imap_and_gmail_paths_are_refused_at_save_and_run(label: str) -> None:
    from email_ingestion.providers.base import move_refusal

    assert move_refusal(label) == MOVE_REFUSED_SENTENCE
    with pytest.raises(HTTPException):
        _rules.refuse_move_into_sent(_req(label).actions)


def test_path_names_of_other_folders_are_allowed() -> None:
    from email_ingestion.providers.base import move_refusal

    for label in ("INBOX.Receipts", "[Gmail]/Spam", "Sentinel"):
        assert move_refusal(label) is None
