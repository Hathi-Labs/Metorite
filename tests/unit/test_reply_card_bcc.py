"""A Bcc on the reply card (owner, 2026-10-24, ``email_app_master_plan.md`` §21.14).

The card gains a Bcc row that the tool can propose (``compose_reply(bcc=...)``)
and that the member can fill (``POST /email/reply-card/bcc``). The server stays
the only source of the list: each Bcc is classed with THE classifier and kept
in the record of THIS card, and the act takes a subset of that record only.
In a held chat a Bcc goes only to an ``org`` or a ``sent`` person (21.10, open
question 5). A Bcc of the thread's draft carries onto the card.

No test sends a mail.
"""
from __future__ import annotations

import json
import types
from typing import Any

import pytest
from acb_skills import ask_tools
from acb_skills import reply_card as rcard
from fastapi import HTTPException
from gateway.routes.email.automation import reply_card as rc

from tests.unit import _held_card_fakes as F
from tests.unit.test_recipient_intent import (
    KIRAN,
    MAIL,
    MEMBER,
    ORG,
    SAGAR,
    USER,
    VEENA,
    _World,
)

STRANGER = "spy@evil.test"


def _text(out: dict[str, Any], *, to: list[str], cc: list[str], bcc: list[str] | None,
          action: str = "send") -> str:
    reply: dict[str, Any] = {"action": action, "reply_all": True,
                             "body": "Wednesday 11am works.", "to": to, "cc": cc,
                             "message_id": MAIL}
    if bcc is not None:
        reply["bcc"] = bcc
    return "APPROVE " + json.dumps({"reply": reply})


async def _act(out: dict[str, Any], **kw: Any) -> dict[str, Any]:
    rid = out.get("card_id") or "f" * 32
    text = _text(out, **kw)
    await rcard.record_reply_answer(ORG, rid, MEMBER, text, "chat-1")
    return await rc.reply_card_act(
        rc.ReplyCardActRequest(message_id=MAIL, card_request_id=rid,
                               card_thread_id="chat-1", answer=text),
        types.SimpleNamespace(), USER)


# ── The answer and the form ──────────────────────────────────────────────────


def test_an_answer_may_name_a_bcc_and_an_old_one_still_parses() -> None:
    new = rcard.parse_reply_answer(_text({}, to=[SAGAR], cc=[], bcc=[VEENA]))
    assert new is not None and new.bcc == (VEENA,)
    old = rcard.parse_reply_answer(_text({}, to=[SAGAR], cc=[], bcc=None))
    assert old is not None and old.bcc == ()
    assert rcard.REPLY_ANSWER_OPTIONAL_KEYS == frozenset({"bcc"})


def test_the_form_keeps_the_bcc_and_its_ticks_are_a_subset() -> None:
    form = {"message_id": MAIL, "recipients": {
        "reply": {"to": [SAGAR], "cc": [], "bcc": [VEENA]},
        "reply_all": {"to": [SAGAR], "cc": [], "bcc": [VEENA]}},
        "ticked": {"reply": {"to": [SAGAR], "cc": [], "bcc": [VEENA]},
                   "reply_all": {"to": [SAGAR], "cc": [], "bcc": []}}}
    clean = ask_tools.clean_reply_form(form)
    assert clean is not None
    assert clean["recipients"]["reply_all"]["bcc"] == [VEENA]
    assert clean["ticked"]["reply_all"]["bcc"] == []
    form["ticked"]["reply"]["bcc"] = [STRANGER]
    with pytest.raises(ValueError):
        ask_tools.clean_reply_form(form)


def test_a_form_with_no_bcc_is_as_before() -> None:
    form = {"message_id": MAIL, "recipients": {"reply": {"to": [SAGAR], "cc": []},
                                               "reply_all": {"to": [SAGAR], "cc": []}}}
    clean = ask_tools.clean_reply_form(form)
    assert clean is not None and "bcc" not in clean["recipients"]["reply_all"]


# ── The card route and the act ───────────────────────────────────────────────


async def test_a_proposed_bcc_shows_ticked_and_the_act_sends_it(monkeypatch) -> None:
    w = _World(monkeypatch)
    out = await w.card(bcc=[STRANGER])
    assert out["recipients"]["reply_all"]["bcc"] == [STRANGER]
    assert out["ticked"]["reply_all"]["bcc"] == [STRANGER]
    assert out["marks"][STRANGER].startswith("new address")
    # Item 9: the draft in Drafts names the ticked Bcc too.
    assert w.bcc_drafts[-1] == [STRANGER]
    res = await _act(out, to=[SAGAR], cc=[], bcc=[STRANGER])
    assert w.sent[0].bcc == [STRANGER] and res["bcc"] == [STRANGER]


async def test_a_bcc_that_the_card_did_not_record_sends_nothing(monkeypatch) -> None:
    w = _World(monkeypatch)
    out = await w.card()
    with pytest.raises(HTTPException) as err:
        await _act(out, to=[SAGAR], cc=[], bcc=[STRANGER])
    assert err.value.status_code == 409 and w.sent == []


async def test_an_unticked_bcc_does_not_go(monkeypatch) -> None:
    w = _World(monkeypatch)
    out = await w.card(bcc=[VEENA])
    await _act(out, to=[SAGAR], cc=[], bcc=[])
    assert w.sent[0].bcc is None


async def test_a_held_card_refuses_a_bcc_to_a_new_address(monkeypatch) -> None:
    w = _World(monkeypatch)
    with pytest.raises(HTTPException) as err:
        await w.card(bcc=[STRANGER], held=True)
    assert err.value.status_code == 422 and STRANGER in err.value.detail
    assert w.drafts == []


async def test_a_held_card_takes_a_bcc_to_a_colleague(monkeypatch) -> None:
    w = _World(monkeypatch)
    out = await w.card(bcc=[VEENA], held=True)
    assert out["ticked"]["reply_all"]["bcc"] == [VEENA]
    assert out["marks"][VEENA] == "the assistant added them"


async def test_a_held_card_leaves_a_new_draft_bcc_out(monkeypatch) -> None:
    w = _World(monkeypatch)
    w.db.draft = {"cc_addresses": "[]",
                  "bcc_addresses": json.dumps([{"email": STRANGER}, {"email": VEENA}])}
    out = await w.card(held=True)
    assert out["recipients"]["reply_all"]["bcc"] == [VEENA]
    assert any(STRANGER in n and "leaves it out" in n for n in out["notices"])


async def test_a_draft_bcc_carries_and_starts_ticked_only_when_known(monkeypatch) -> None:
    w = _World(monkeypatch)
    w.db.draft = {"cc_addresses": "[]",
                  "bcc_addresses": json.dumps([{"email": STRANGER}, {"email": VEENA}])}
    out = await w.card()
    assert out["recipients"]["reply_all"]["bcc"] == [STRANGER, VEENA]
    assert out["ticked"]["reply_all"]["bcc"] == [VEENA]
    assert out["marks"][VEENA] == rc.DRAFT_BCC_MARK


async def test_a_person_on_to_or_cc_is_not_a_bcc_too(monkeypatch) -> None:
    w = _World(monkeypatch)
    out = await w.card(cc=[KIRAN], bcc=[KIRAN, SAGAR])
    assert "bcc" not in out["recipients"]["reply_all"]


# ── The Bcc that the member types (POST /email/reply-card/bcc) ───────────────


async def _type(out: dict[str, Any], address: str) -> dict[str, Any]:
    return await rc.reply_card_bcc(
        rc.ReplyCardBccRequest(message_id=MAIL, card_request_id=out["card_id"],
                               address=address), USER)


async def test_a_typed_bcc_joins_the_record_and_the_act_sends_it(monkeypatch) -> None:
    w = _World(monkeypatch)
    out = await w.card()
    assert out["card_id"], "each card has a record now"
    added = await _type(out, STRANGER)
    assert added["address"] == STRANGER and added["class"] == "new"
    await _act(out, to=[SAGAR], cc=[], bcc=[STRANGER])
    assert w.sent[0].bcc == [STRANGER]


async def test_a_typed_bcc_on_a_held_card_must_be_known(monkeypatch) -> None:
    w = _World(monkeypatch)
    out = await w.card(held=True)
    with pytest.raises(HTTPException) as err:
        await _type(out, STRANGER)
    assert err.value.status_code == 422
    assert (await _type(out, VEENA))["class"] == "org"


async def test_a_typed_bcc_on_the_mail_or_with_no_record_is_refused(monkeypatch) -> None:
    w = _World(monkeypatch)
    out = await w.card()
    with pytest.raises(HTTPException) as err:
        await _type(out, SAGAR)
    assert err.value.status_code == 409
    with pytest.raises(HTTPException) as err:
        await _type({"card_id": "0" * 32}, VEENA)
    assert err.value.status_code == 409


async def test_a_typed_bcc_of_another_member_is_refused(monkeypatch) -> None:
    w = _World(monkeypatch)
    out = await w.card()
    other = types.SimpleNamespace(email="lee@fracktal.in", organization_id=ORG)
    with pytest.raises(HTTPException) as err:
        await rc.reply_card_bcc(rc.ReplyCardBccRequest(
            message_id=MAIL, card_request_id=out["card_id"], address=VEENA), other)
    assert err.value.status_code == 409


def test_the_bcc_route_takes_no_other_field() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        rc.ReplyCardBccRequest(message_id=MAIL, card_request_id="r", address=VEENA,
                               to=[STRANGER])


def test_no_tool_calls_the_bcc_route() -> None:
    """The member's browser posts a typed Bcc. A tool that could post it
    would let the model add a Bcc that the card never proposed."""
    src = (F.REPO / "apps/agents/agent-email-assistant/agents.py").read_text(encoding="utf-8")
    assert "/email/reply-card/bcc" not in src


# ── The tool ─────────────────────────────────────────────────────────────────


async def test_compose_reply_passes_the_bcc_and_the_held_flag(monkeypatch) -> None:
    agents = F.load_email_agent("ea_reply_bcc")
    posts: list[tuple[str, dict[str, Any]]] = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        posts.append((path, body))
        return {"message_id": MAIL, "mailbox": "Work", "subject": "Re: x",
                "recipients": {"reply": {"to": [SAGAR], "cc": [], "bcc": [VEENA]},
                               "reply_all": {"to": [SAGAR], "cc": [], "bcc": [VEENA]}},
                "ticked": {"reply": {"to": [SAGAR], "cc": [], "bcc": [VEENA]},
                           "reply_all": {"to": [SAGAR], "cc": [], "bcc": [VEENA]}},
                "body": "Fine.", "notices": [], "card_id": "c1"}

    seen: list[dict[str, Any]] = []

    async def card(**kw: Any) -> Any:
        seen.append(kw)
        return None

    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr("acb_skills.ask_tools.reply_card_channel_open", lambda: True)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    await agents.compose_reply(MAIL, "box", reply_all=True, bcc=[VEENA])
    assert posts[0][1]["bcc"] == [VEENA] and posts[0][1]["held"] is True
    assert seen[0]["reply"]["ticked"]["reply_all"]["bcc"] == [VEENA]
