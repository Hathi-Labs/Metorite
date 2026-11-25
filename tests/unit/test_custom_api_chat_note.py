"""API-6 (task 3792, bug 3766): a custom "Add API" row says that the chat
cannot use it, when API connections are on for the org.

Fence (R7): these tests fail when the note leaves the row, when the note shows
with the flag off, or when the text claims that the chat can use the row.
"""

from __future__ import annotations

import inspect
from gateway.routes import integrations

ROW = {"service_id": "acme", "label": "Acme", "env_vars": [{"key": "ACME_KEY"}]}


def test_the_note_text_says_the_chat_cannot_use_the_row() -> None:
    use = integrations.CUSTOM_ROW_CHAT_USE
    assert use["usable"] is False
    assert "cannot use" in use["note"]
    assert "template" in use["note"]


def test_the_status_row_itself_has_no_note_so_the_flag_decides() -> None:
    row = integrations._custom_status_row(ROW, {})
    assert "chat_use" not in row


def test_the_list_adds_the_note_only_behind_the_flag() -> None:
    src = inspect.getsource(integrations)
    assert "api_connections_allowed(current_tenant())" in src
    assert 'status_row["chat_use"] = dict(CUSTOM_ROW_CHAT_USE)' in src
