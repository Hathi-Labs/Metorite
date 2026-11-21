"""HRD-8b-3 beside HRD-10d-3: ONE reply card that holds both.

With ``EMAIL_EDIT_CARD_ORGS`` and ``CHAT_CARD_PRECONDITION_ORGS`` both on,
the reply card names its chat thread (8b-3) and the head of the mail thread
(10d-3). The record then holds ``kind``, ``thread``, ``to``, ``cc``, the link
and ``thread_head``. A revise through a chat message spends it and links the
next card by the CARD id, never by the head. The act of the next card still
sends the head as ``expect_thread``. Mutation (R7): drop
``thread_head=head.get("thread_head", "")`` from the ``_write_reply_record``
call in ``reply_card.reply_card`` -> red.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from acb_skills import ask_tools as at
from fastapi import HTTPException
from gateway.routes.email.automation import redraft as _rd
from gateway.routes.email.automation import reply_card as _rc

from tests.unit import test_email_redraft_snapshot as snap
from tests.unit import test_email_thread_changed as tc
from tests.unit.test_email_thread_changed import gw  # noqa: F401  (a fixture, by name)
from tests.unit.test_recipient_intent import ANANDA, SAGAR, USER, _World

HEAD = tc.HEAD
CHAT = "chat-thread-1"


def _key(w: Any, card_id: str) -> str:
    return next(k for k in w.redis.keys if k.endswith(f":reply-card:{card_id}"))


def _world(monkeypatch: pytest.MonkeyPatch, *, edit: str = "*") -> Any:
    snap.edit_card(monkeypatch, edit)
    w = _World(monkeypatch)

    async def head(_mail: Any, _owner: str) -> dict[str, str]:
        return {"thread_head": HEAD}

    monkeypatch.setattr(_rc, "_card_thread_head", head)
    return w


async def _card(w: Any, thread: str) -> dict[str, Any]:
    return await w.card(card_thread_id=thread, thread_head=True)


async def test_one_card_holds_the_thread_and_the_head_and_a_revise_keeps_the_head(monkeypatch):
    w = _world(monkeypatch)
    first = await _card(w, CHAT)
    rec = json.loads(w.redis.keys[_key(w, first["card_id"])])
    assert rec["kind"] == "reply" and rec["thread"] == CHAT
    assert rec["to"] and isinstance(rec["cc"], list)
    assert rec["thread_head"] == HEAD
    assert "revises" not in rec  # the first card of the chat takes no link

    # A chat message closes the card: the revise spends it and links by the CARD id.
    out = await _rd.revise_new_mail_card(
        _rd.ReviseRequest(card_id=first["card_id"], card_thread_id=CHAT), USER)
    assert out["revised"] is True and out["draft"] == 2
    [link] = [json.loads(v) for k, v in w.redis.keys.items() if ":card_revised:" in k]
    assert link["card_id"] == first["card_id"] != HEAD and link["act"] == "reply"

    # The next card holds the link AND the head, and the head is not the link.
    second = await _card(w, CHAT)
    rec = json.loads(w.redis.keys[_key(w, second["card_id"])])
    assert rec["revises"] == first["card_id"] and rec["draft"] == 2
    assert rec["thread_head"] == HEAD and rec["kind"] == "reply" and rec["thread"] == CHAT

    # The act of that card sends the head of its record as the marker.
    await w.act(second, to=[SAGAR], cc=[ANANDA])
    [sent] = w.sent
    assert sent.expect_thread == HEAD


async def test_a_card_with_the_head_and_no_thread_is_not_revised_and_keeps_the_head(monkeypatch):
    """Edit card off, thread check on: the record is the 10d-3 record only."""
    w = _world(monkeypatch, edit="")
    card = await _card(w, CHAT)
    rec = json.loads(w.redis.keys[_key(w, card["card_id"])])
    assert rec["thread_head"] == HEAD
    assert not {"kind", "thread", "to", "cc", "revises", "draft", "changed"} & set(rec)
    snap.edit_card(monkeypatch, "*")
    with pytest.raises(HTTPException) as err:
        await _rd.revise_new_mail_card(
            _rd.ReviseRequest(card_id=card["card_id"], card_thread_id=CHAT), USER)
    assert err.value.status_code == 404
    await w.act(card, to=[SAGAR], cc=[ANANDA])
    assert w.sent[0].expect_thread == HEAD


async def test_both_flags_off_keep_the_record_of_before(monkeypatch):
    w = _world(monkeypatch, edit="")
    card = await w.card(card_thread_id=CHAT)
    rec = json.loads(w.redis.keys[_key(w, card["card_id"])])
    assert not {"kind", "thread", "to", "cc", "revises", "draft", "changed",
                "thread_head"} & set(rec)


async def test_the_tool_asks_for_both_and_spends_a_closed_card(gw, monkeypatch):  # noqa: F811
    """compose_reply with both flags on sends ``card_thread_id`` and
    ``thread_head``, and a card that a chat message closed is revised."""
    tc._flag(monkeypatch, tc.ORG)
    monkeypatch.setattr(tc.agents, "_edit_card_open", lambda: True)
    monkeypatch.setattr(tc.agents, "_run_thread", lambda: CHAT)
    gw.prep_extra = {"thread_head": HEAD, "card_id": "c" * 32}
    gw.answer = at.REVISED
    await tc._bound(tc.agents.compose_reply(tc.MAIL, tc.BOX))
    prep, revise = gw.posts[0], gw.posts[-1]
    assert prep == ("/email/reply-card", {"message_id": tc.MAIL,
                                          "card_thread_id": CHAT, "thread_head": True})
    assert revise == ("/email/new-card/revise", {"card_id": "c" * 32, "card_thread_id": CHAT})
    assert gw.facts == [{"thread_head": HEAD}]
