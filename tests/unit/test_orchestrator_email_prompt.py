"""The orchestrator's email rules (``navigation_shell.md`` §7.1.1 rule 7).

R7 fence ``one-assistant-email-prompt``. The prompt is the only lever on the
model's choice, so this suite holds its words:

* the orchestrator copies the ids of the working set and the mailbox list
  into each email_assistant task, and the draft_id for "send it";
* a reply, a Cc and a send asked together go in ONE task;
* it never sends the member to the Email app, except to reconnect.

Run::

    uv run pytest tests/unit/test_orchestrator_email_prompt.py -v
"""
from __future__ import annotations

import re


def _prompt() -> str:
    # ``orchestrator.agents`` is a package that loads ``agents.py`` as
    # ``_mod`` (see its ``__init__``), as test_orchestrator_voice.py reads it.
    import orchestrator.agents as pkg

    return " ".join(pkg._mod._PULL_INSTRUCTIONS.split())


def test_it_passes_the_ids_of_the_working_set() -> None:
    text = _prompt()
    assert '"Email working set of this chat"' in text
    assert "\"The member's mailboxes\"" in text
    assert "the email_id, the draft_id and the account_id or mailbox label" in text
    assert re.search(r'For "send it" or "send the draft", give the draft_id', text)


def test_reply_cc_and_send_go_in_one_task() -> None:
    text = _prompt()
    assert "a reply, a Cc and a send in one message, give email_assistant ONE task" in text
    assert "Never split one request into two tasks." in text


def test_it_never_sends_the_member_to_the_email_app() -> None:
    text = _prompt()
    assert "Never tell the member to open the Email app to do it." in text
    assert "Only a mailbox that needs reconnecting needs the Email app" in text


def test_with_no_mailbox_named_it_names_none() -> None:
    assert "When the member names no mailbox, name none" in _prompt()
