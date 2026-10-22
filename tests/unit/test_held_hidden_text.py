"""No hidden character leaves in a draft from the chat (§21 fix round 2).

The review found the one send path with no check of hidden text. The draft
card showed ``clean_reply_body(text)``, and the send carried the raw draft.
So tag characters (U+E0020 to U+E007F) or zero-width bits could carry member
data past a card that looked clean. The seal folded them in, so it covered
the data and did not catch it. This held with the switch OFF too (H-374).

The fix: ``reply_card.has_hidden_text`` is the one test. A chat send
(``expect``) refuses such a draft at the card, at the send and at the fire of
a schedule, before any provider write. ``edit_draft`` and a chat save
(``from_chat``) refuse such a text, and they normalise NBSP and trailing
spaces. The Email app's composer is unchanged.

R7, each with the mutation that turns it red:

* ``test_the_rule_*`` — make ``has_hidden_text`` read only the bidi controls.
* ``test_a_draft_with_hidden_text_is_refused_at_send_and_at_fire`` — drop the
  check in ``send_draft_endpoint``.
* ``test_the_seal_refuses_hidden_text_at_the_card`` — drop the check in
  ``draft_text_seal``.
* ``test_edit_draft_refuses_hidden_text`` and
  ``test_a_chat_save_refuses_hidden_text`` — drop either check.
* ``test_nbsp_and_trailing_spaces_are_normalised_and_still_send`` — refuse
  them, or save them raw.
* ``test_the_composer_save_is_unchanged`` — apply the chat rule to every save.
* ``test_the_held_card_refuses_a_hidden_draft`` — drop ``_refuse_hidden_draft``.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from acb_skills.reply_card import has_hidden_text, visible_spacing
from fastapi import HTTPException
from gateway.routes.email.automation import drafting as d
from gateway.routes.email.automation import send_later as sl

from tests.unit import _held_card_fakes as F
from tests.unit.test_held_text_seal import _drow, _Provider, _seal, _send

TAG = "Hi Kim,\n\nThe quote.\U000e0070\U000e0061\U000e0079"  # "pay" in tags
ZERO_WIDTH = "Hi Kim,\n\nThe​ quote‌."
NBSP = "Hi\u00a0Kim,   \n\nThe quote.  "


@pytest.mark.parametrize(("text", "hidden"), [
    (TAG, True), (ZERO_WIDTH, True), ("a‮b", True), ("a⁠b", True),
    (NBSP, False), ("Hi Kim,\n\n\n\nThanks.\r\n", False), ("नमस्ते, ধন্যবাদ", False),
    ("👨‍👩‍👧 and ❤️", False),
])
def test_the_rule_reads_hidden_text(text: str, hidden: bool) -> None:
    assert has_hidden_text(text) is hidden


def test_the_rule_normalises_nbsp_and_trailing_spaces() -> None:
    assert visible_spacing(NBSP) == "Hi Kim,\n\nThe quote."


@pytest.mark.parametrize("body", [TAG, ZERO_WIDTH], ids=["tags", "zero-width"])
@pytest.mark.parametrize("digest", [None, "x" * 64], ids=["fire-of-an-old-schedule", "send"])
async def test_a_draft_with_hidden_text_is_refused_at_send_and_at_fire(
    body: str, digest: str | None,
) -> None:
    """``digest`` None is a schedule stored before the seal, which fires
    through the same send. Nothing is written to the provider."""
    p = _Provider(live=body)
    with pytest.raises(HTTPException) as e:
        await _send(_drow(body_text=body), p, digest)
    assert e.value.status_code == 422 and e.value.detail == d.DRAFT_HIDDEN_TEXT_DETAIL
    assert p.sent == [] and p.updated == []


def test_the_fire_names_the_reason() -> None:
    why = sl._why(HTTPException(status_code=422, detail=d.DRAFT_HIDDEN_TEXT_DETAIL))
    assert "hidden characters" in why and "not sent" in why


@pytest.mark.parametrize("where", ["stored", "subject", "live"])
async def test_the_seal_refuses_hidden_text_at_the_card(where: str) -> None:
    row = _drow(body_text=TAG) if where == "stored" else (
        _drow(subject="Quote​") if where == "subject" else _drow())
    p = _Provider(live=ZERO_WIDTH if where == "live" else row.body_text)
    with pytest.raises(d.HiddenDraftText):
        await _seal(row, p)


async def test_nbsp_and_trailing_spaces_are_normalised_and_still_send() -> None:
    p = _Provider(live=NBSP)
    row = _drow(body_text=NBSP)
    await _send(row, p, await _seal(row, p))
    assert p.sent == ["pm-1"]


def test_a_chat_save_refuses_hidden_text_and_normalises_spacing() -> None:
    def req(body: str, subject: str = "Quote") -> Any:
        return d.DraftUpsertRequest(account_id=F.BOX, draft_id=F.DRAFT, body=body,
                                    subject=subject, from_chat=True)

    for body, subject in ((TAG, "Quote"), (ZERO_WIDTH, "Quote"), ("Hi", "Quote​")):
        with pytest.raises(HTTPException) as e:
            d._chat_draft_text(req(body, subject))
        assert e.value.status_code == 422
    clean = d._chat_draft_text(req(NBSP, "Quote\u00a0for  June "))
    assert clean.body == "Hi Kim,\n\nThe quote." and clean.subject == "Quote for June"


async def test_the_composer_save_is_unchanged(monkeypatch) -> None:
    """The Email app sends no ``from_chat``. Its save goes on with the text as
    typed, NBSP included, and the chat rule never runs."""
    assert d.DraftUpsertRequest(account_id=F.BOX).from_chat is False
    ran: list[str] = []

    def chat_rule(req: Any) -> Any:
        ran.append("chat")
        return req

    class _Reached(Exception):
        pass

    def session():
        raise _Reached

    monkeypatch.setattr(d, "_chat_draft_text", chat_rule)
    monkeypatch.setattr(d, "_tenant_session", session)
    with pytest.raises(_Reached):
        await d.upsert_draft(d.DraftUpsertRequest(account_id=F.BOX, body=NBSP),
                             SimpleNamespace(email=F.MEMBER))
    assert ran == []
    with pytest.raises(_Reached):
        await d.upsert_draft(d.DraftUpsertRequest(account_id=F.BOX, body="x", from_chat=True),
                             SimpleNamespace(email=F.MEMBER))
    assert ran == ["chat"]


@pytest.fixture()
def tool(monkeypatch):
    from acb_skills import ask_tools
    from acb_skills.write_artifact import bind_artifact_context

    agents = F.load_email_agent("ea_held_hidden")
    bind_artifact_context(no_egress=True, agent_name="email-assistant", session_id="s-1")
    calls: dict[str, list[Any]] = {"put": [], "cards": [], "gets": []}

    async def get(path: str, params: Any = None) -> Any:
        calls["gets"].append(path)
        if path == f"/email/messages/{F.DRAFT}":
            return {"account_id": F.BOX, "folder": "drafts", "subject": "Quote",
                    "to_addresses": [{"email": F.KNOWN}], "body_text": "Hi"}
        if path == "/email/drafts/seal":
            raise agents.GatewayError("Email GET /email/drafts/seal failed (422)", 422,
                                      detail=d.DRAFT_HIDDEN_TEXT_DETAIL)
        if path == "/email/drafts/scheduled":
            return []
        raise AssertionError(path)

    async def put(path: str, body: Any) -> Any:
        calls["put"].append(body)
        return {}

    async def mailbox(_aid: str) -> str:
        return F.MEMBER

    async def card(**kw: Any) -> bool:
        calls["cards"].append(kw)
        return True

    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_put", put)
    monkeypatch.setattr(agents, "_mailbox_name", mailbox)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: True)
    return agents, calls


@pytest.mark.parametrize("body", [TAG, ZERO_WIDTH], ids=["tags", "zero-width"])
async def test_edit_draft_refuses_hidden_text(tool, body: str) -> None:
    agents, calls = tool
    out = await agents.edit_draft(F.BOX, F.DRAFT, body=body)
    assert out.startswith("Nothing changed.") and "hidden characters" in out
    assert calls["put"] == [] and calls["cards"] == []


async def test_edit_draft_normalises_spacing_and_marks_the_save(tool) -> None:
    agents, calls = tool
    await agents.edit_draft(F.BOX, F.DRAFT, body=NBSP)
    assert calls["put"][0]["body"] == "Hi Kim,\n\nThe quote."
    assert calls["put"][0]["from_chat"] is True


@pytest.mark.parametrize("name", ["send_draft", "schedule_send"])
async def test_a_send_card_of_a_hidden_draft_shows_no_card(tool, name: str) -> None:
    from datetime import UTC, datetime, timedelta

    agents, calls = tool
    args = [F.BOX, F.DRAFT] + ([(datetime.now(UTC) + timedelta(days=1)).isoformat()]
                               if name == "schedule_send" else [])
    out = await getattr(agents, name)(*args)
    assert "hidden characters, so I cannot send it" in out
    assert calls["cards"] == []


async def test_the_held_card_refuses_a_hidden_draft(monkeypatch) -> None:
    from gateway.routes.email.automation import held_card as hc

    F.flag_on(monkeypatch)
    F.install_redis(monkeypatch)
    db = F.install_db(monkeypatch)
    db.draft["body_text"] = TAG
    with pytest.raises(HTTPException) as e:
        await hc.held_card(hc.HeldCardRequest(kind="draft", account_id=F.BOX,
                                              draft_id=F.DRAFT), F.user())
    assert e.value.status_code == 422 and e.value.detail == d.DRAFT_HIDDEN_TEXT_DETAIL
