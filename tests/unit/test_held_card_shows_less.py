"""A send card never shows less than the send carries (§21 fix round 3).

The re-review found the class twice more. The draft card cut the subject at
120 characters (the held card at 200), and the send carried the whole one, so
a model could hide data at the end of a long, visible subject. The switch-off
card cut a long body with a "the rest is cut here" mark, and the member still
approved the whole text. A card also cannot show NBSP or a space at the end of
a line. Each held with the switch OFF too (H-374).

The fix: ``drafting.check_draft_send`` is the one check of a chat send of a
draft: no hidden character, a subject of ``SUBJECT_MAX`` (200) at most, and
no NBSP and no line-end space. The card (the seal), the send and the fire of a
schedule run it. The card shows the whole subject, and a body that does not
fit shows no card. The chat writers save no NBSP and no line-end space.

R7, each with the mutation that turns it red:

* ``test_a_long_subject_is_refused_at_the_card_the_send_and_the_fire`` —
  drop the subject cap of ``check_draft_send``.
* ``test_edit_draft_and_a_chat_save_refuse_a_long_subject`` — drop the cap
  of the tool or of ``_chat_draft_text``.
* ``test_the_card_shows_the_whole_subject`` — cut the subject again.
* ``test_a_long_body_shows_no_send_card`` — let ``whole`` cut.
* ``test_a_line_end_space_from_another_writer_is_refused`` — drop the
  spacing check.
* ``test_a_chat_draft_from_the_drafter_sends_cleanly`` — drop
  ``strip_invisible_spacing`` from ``_upsert_reply_draft``.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.email.automation import drafting as d
from gateway.routes.email.automation import send_later as sl

from tests.unit import _held_card_fakes as F
from tests.unit.test_held_hidden_text import tool  # noqa: F401 - a fixture
from tests.unit.test_held_text_seal import _drow, _Provider, _seal, _send

LONG = "Quote for June " + "x" * 186  # 201 characters
assert len(LONG) == 201
EXACT = LONG[:200]


async def test_a_long_subject_is_refused_at_the_card_the_send_and_the_fire() -> None:
    with pytest.raises(d.DraftTextRefused) as e:  # the card: the seal route
        await _seal(_drow(subject=LONG), _Provider())
    assert e.value.detail == d.DRAFT_SUBJECT_TOO_LONG_DETAIL
    for digest in (None, "x" * 64):  # the fire of an old schedule, and a send
        p = _Provider()
        with pytest.raises(HTTPException) as he:
            await _send(_drow(subject=LONG), p, digest)
        assert he.value.status_code == 422
        assert he.value.detail == d.DRAFT_SUBJECT_TOO_LONG_DETAIL
        assert p.sent == []
    assert "200 characters" in sl._why(
        HTTPException(status_code=422, detail=d.DRAFT_SUBJECT_TOO_LONG_DETAIL))
    # 200 characters still send.
    p = _Provider()
    row = _drow(subject=EXACT)
    await _send(row, p, await _seal(row, p))
    assert p.sent == ["pm-1"]


async def test_edit_draft_and_a_chat_save_refuse_a_long_subject(tool) -> None:  # noqa: F811
    agents, calls = tool
    out = await agents.edit_draft(F.BOX, F.DRAFT, subject=LONG)
    assert out.startswith("Nothing changed.") and "200 characters" in out
    assert calls["put"] == [] and calls["cards"] == []
    with pytest.raises(HTTPException) as e:
        d._chat_draft_text(d.DraftUpsertRequest(
            account_id=F.BOX, draft_id=F.DRAFT, subject=LONG, body="Hi", from_chat=True))
    assert e.value.status_code == 422 and e.value.detail == d.DRAFT_SUBJECT_TOO_LONG_DETAIL


def test_the_card_shows_the_whole_subject() -> None:
    agents = F.load_email_agent("ea_held_subject")
    detail, context = agents._draft_card("Work", EXACT, {"to": [F.KNOWN], "cc": [], "bcc": []})
    assert f"Subject: {EXACT}" in context.splitlines()
    assert "Subject" not in detail


async def test_the_held_card_shows_the_whole_subject(monkeypatch) -> None:
    from gateway.routes.email.automation import held_card as hc

    F.flag_on(monkeypatch)
    F.install_redis(monkeypatch)
    db = F.install_db(monkeypatch)
    db.draft["subject"] = EXACT
    card = await hc.held_card(hc.HeldCardRequest(kind="draft", account_id=F.BOX,
                                                 draft_id=F.DRAFT), F.user())
    assert f"Subject: {EXACT}" in card["context"].splitlines()
    db.draft["subject"] = LONG
    with pytest.raises(HTTPException) as e:
        await hc.held_card(hc.HeldCardRequest(kind="draft", account_id=F.BOX,
                                              draft_id=F.DRAFT), F.user())
    assert e.value.detail == d.DRAFT_SUBJECT_TOO_LONG_DETAIL


@pytest.mark.parametrize("name", ["send_draft", "schedule_send"])
async def test_a_long_body_shows_no_send_card(monkeypatch, name: str) -> None:
    from acb_skills import ask_tools

    agents = F.load_email_agent(f"ea_held_long_{name}")
    cards: list[Any] = []
    posts: list[Any] = []

    async def get(path: str, params: Any = None) -> Any:
        if path == f"/email/messages/{F.DRAFT}":
            return {"account_id": F.BOX, "folder": "drafts", "subject": "Quote",
                    "to_addresses": [{"email": F.KNOWN}]}
        if path == "/email/drafts/seal":
            return {"text_digest": "a" * 64, "subject": "Quote", "body_text": "y " * 3000}
        raise AssertionError(path)

    async def post(path: str, body: Any) -> Any:
        posts.append(path)
        return {}

    async def mailbox(_aid: str) -> str:
        return F.MEMBER

    async def card(**kw: Any) -> bool:
        cards.append(kw)
        return True

    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_mailbox_name", mailbox)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    args = [F.BOX, F.DRAFT] + ([(datetime.now(UTC) + timedelta(days=1)).isoformat()]
                               if name == "schedule_send" else [])
    out = await getattr(agents, name)(*args)
    assert cards == [] and posts == []
    assert "too long to show on the card" in out
    # The Email app is one choice in the answer, never the only way.
    assert "shorten it with edit_draft" in out


@pytest.mark.parametrize("body", ["Hi Kim, \nThe quote.", "Hi Kim,\t\nThe quote.",
                                  "Hi\u00a0Kim,\nThe quote."],
                         ids=["space", "tab", "nbsp"])
async def test_a_line_end_space_from_another_writer_is_refused(body: str) -> None:
    p = _Provider(live=body)
    with pytest.raises(HTTPException) as e:
        await _send(_drow(body_text=body), p, None)
    assert e.value.detail == d.DRAFT_SPACING_DETAIL and p.sent == []
    with pytest.raises(HTTPException):
        await _send(_drow(subject="Quote\u00a0June"), _Provider(), None)


async def test_a_chat_draft_from_the_drafter_sends_cleanly() -> None:
    """The drafter of /email/draft-reply writes through ``_upsert_reply_draft``
    with the signature in the text. The signature delimiter is "-- " with a
    line-end space, and a name can hold NBSP. The draft it saves passes the
    check of a chat send."""
    from tests.unit.test_email_reply_draft_upsert import ACC, THREAD, _Db, _Outlook

    db, provider = _Db(), _Outlook()
    drafted = "Hi Kim,  \n\nYes, Friday works.\n\n-- \nJane\u00a0Doe\u00a0"
    wrote = await d._upsert_reply_draft(
        db, provider, ACC, thread_id=THREAD, reply_to_provider_id="pm-mail",
        owner_email=F.MEMBER, to=[F.KNOWN], subject="Re:\u00a0Quote", body=drafted)
    sent_text = provider.created[0]["body_text"]
    row = next(r for r in db.rows if r["id"] == wrote.local_id)
    d.check_draft_send(provider.created[0]["subject"], sent_text)
    d.check_draft_send("Re: Quote", row["body"])
    assert sent_text == "Hi Kim,\n\nYes, Friday works.\n\n--\nJane Doe"


# ── Fix round 4 ──────────────────────────────────────────────────────────────


async def test_a_drafter_body_with_blank_runs_is_saved_folded() -> None:
    """Each chat writer saves the form that the card shows: a run of blank
    lines is one blank line, and the edges are trimmed. Mutation: go back to
    ``strip_invisible_spacing`` in ``_upsert_reply_draft`` → red."""
    from tests.unit.test_email_reply_draft_upsert import ACC, THREAD, _Db, _Outlook

    db, provider = _Db(), _Outlook()
    drafted = "\n\nHi Kim,\n\n\n\n\nYes, Friday works.\n\n\n"
    wrote = await d._upsert_reply_draft(
        db, provider, ACC, thread_id=THREAD, reply_to_provider_id="pm-mail",
        owner_email=F.MEMBER, to=[F.KNOWN], subject="Re: Quote", body=drafted)
    saved = provider.created[0]["body_text"]
    assert saved == "Hi Kim,\n\nYes, Friday works."
    row = next(r for r in db.rows if r["id"] == wrote.local_id)
    assert row["body"] == saved


@pytest.mark.parametrize("name", ["send_draft", "schedule_send"])
async def test_the_send_card_lists_the_files_of_the_draft(monkeypatch, name: str) -> None:
    """The switch-off card names each file, in the line form of the held
    card (``held_card._draft_card``). Mutation: drop ``_draft_files_block``
    from ``_sealed_card`` → red."""
    from acb_skills import ask_tools

    agents = F.load_email_agent(f"ea_held_files_{name}")
    cards: list[Any] = []

    async def get(path: str, params: Any = None) -> Any:
        if path == f"/email/messages/{F.DRAFT}":
            return {"account_id": F.BOX, "folder": "drafts", "subject": "Quote",
                    "to_addresses": [{"email": F.KNOWN}],
                    "attachments": [{"id": "f1", "filename": "quote.pdf", "size_bytes": 4096},
                                    {"id": "f2", "filename": "rates.xlsx", "size_bytes": 2048}]}
        if path == "/email/drafts/seal":
            return {"text_digest": "a" * 64, "subject": "Quote", "body_text": "Hi Kim"}
        raise AssertionError(path)

    async def post(path: str, body: Any) -> Any:
        return {"id": "s-1"}

    async def mailbox(_aid: str) -> str:
        return F.MEMBER

    async def card(**kw: Any) -> bool:
        cards.append(kw)
        return True

    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_mailbox_name", mailbox)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    args = [F.BOX, F.DRAFT] + ([(datetime.now(UTC) + timedelta(days=1)).isoformat()]
                               if name == "schedule_send" else [])
    await getattr(agents, name)(*args)
    lines = cards[0]["context"].splitlines()
    i = lines.index("The files of the draft go with it:")
    assert lines[i + 1:i + 3] == ['- file "quote.pdf" (4 KB)', '- file "rates.xlsx" (2 KB)']
