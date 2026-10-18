"""WS-17 — what the assistant says when a text search was cut (incident 2026-10-18).

Spec: ``project-docs/specs/email_app_master_plan.md`` §18. The gateway matches
the newest ``match_cap`` mails at most, and the cut comes BEFORE the folder,
date, sender and read filters. So an empty page of a cut search does not mean
that no mail matched. Older mail with the word was not searched.

R7 fence ``email-search-cut-wording``: a cut search with an empty page never
says "No emails matched", and the note never advises a date range, because
a date range does not reach mail past the cut.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

AGENT_DIR = Path(__file__).resolve().parents[2] / "apps" / "agents" / "agent-email-assistant"
_SPEC = importlib.util.spec_from_file_location("ws17_cut_wording_agents", AGENT_DIR / "agents.py")
assert _SPEC and _SPEC.loader
agents = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(agents)

ACCOUNT = "a0000000-0000-4000-8000-000000000001"
EXPECTED = (
    "This word is in more than 20,000 of your mails, so only the newest 20,000 "
    "were searched, and none of them fit these filters. Older mail was not "
    "searched. Add a more specific word, a name or a phrase, to search further "
    "back.")


def _gateway(monkeypatch, body: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append({"path": path, "params": dict(params or {})})
        return body

    monkeypatch.setattr(agents, "_get", fake_get)
    return calls


async def test_a_cut_search_with_an_empty_page_says_older_mail_was_not_searched(
        monkeypatch: pytest.MonkeyPatch) -> None:
    _gateway(monkeypatch, {"emails": [], "total": 0, "truncated": True,
                           "match_cap": 20000})
    out = await agents.query_inbox(account_id=ACCOUNT, query="order", days=3650)
    assert out == EXPECTED
    assert "No emails matched" not in out


async def test_a_full_search_with_an_empty_page_still_says_no_match(
        monkeypatch: pytest.MonkeyPatch) -> None:
    _gateway(monkeypatch, {"emails": [], "total": 0, "truncated": False,
                           "match_cap": 20000})
    out = await agents.query_inbox(account_id=ACCOUNT, query="zirconia")
    assert out == "No emails matched those filters."


async def test_the_cut_note_advises_a_more_specific_word_not_a_date(
        monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"id": "m1", "subject": "Re: order", "from_address": {"name": "Ravi"},
           "received_at": "2026-10-01T10:00:00+00:00", "snippet": "the order",
           "is_read": True}
    _gateway(monkeypatch, {"emails": [row], "total": 20000, "truncated": True,
                           "match_cap": 20000})
    for out in (await agents.query_inbox(account_id=ACCOUNT, query="order"),
                await agents.search_emails(query="order", account_id=ACCOUNT)):
        assert "These are the newest 20,000 matches." in out
        assert "a more specific word, a name or a phrase" in out
        assert "date" not in out.split("matches.", 1)[1].split("\n", 1)[0]
