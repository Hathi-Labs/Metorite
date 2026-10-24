"""No ticked To row sends nothing (``email_app_master_plan.md`` §21.4).

"The rule for a send with no ticked recipient": the send does not fall back
to the draft. If no To row is ticked, nothing leaves, and the tool tells the
model "Not sent: no recipient was ticked".

R7, each with the mutation that turns it red:

* ``test_no_ticked_to_row_sends_nothing`` — make ``held_card_act`` send
  ``rec["held"]`` when nothing is ticked, or drop the To check.
* ``test_a_ticked_cc_alone_does_not_fall_back_to_the_draft`` — the same,
  for a card where only a Cc was ticked.
* ``test_the_tool_says_not_sent`` — let the tool hide the refusal.

The gateway, Redis and the provider are fakes. No test sends a mail.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.automation import held_card as hc

from tests.unit import _held_card_fakes as F


@pytest.fixture()
def world(monkeypatch):
    F.flag_on(monkeypatch)
    monkeypatch.setattr(hc, "ANSWER_WAIT_S", 0.0)
    F.install_redis(monkeypatch)
    F.install_db(monkeypatch)
    return F.install_sends(monkeypatch)


async def _card() -> dict[str, Any]:
    return await hc.held_card(
        hc.HeldCardRequest(kind="draft", account_id=F.BOX, draft_id=F.DRAFT), F.user())


async def _act(card_id: str, answer: str) -> dict[str, Any]:
    return await hc.held_card_act(
        hc.HeldCardActRequest(card_id=card_id, card_thread_id="chat-thread-1", answer=answer),
        BackgroundTasks(), F.user())


async def test_no_ticked_to_row_sends_nothing(world) -> None:
    card = await _card()
    cc = [r["id"] for r in card["rows"] if r["kind"] == "cc"]
    answer = F.rows_answer(*cc)
    await F.record_answer(card["card_id"], answer)
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], answer)
    assert e.value.status_code == 422
    assert e.value.detail == "Not sent: no recipient was ticked."
    assert world.sent == [] and world.narrowed == []


async def test_a_ticked_cc_alone_does_not_fall_back_to_the_draft(world) -> None:
    """The draft holds a To. The member ticked one Cc. Nothing goes, and the
    act does not use the draft's To in place of the ticked rows."""
    card = await _card()
    cc = next(r["id"] for r in card["rows"] if r["kind"] == "cc" and r["checked"])
    answer = F.rows_answer(cc)
    await F.record_answer(card["card_id"], answer)
    with pytest.raises(HTTPException):
        await _act(card["card_id"], answer)
    assert world.sent == []


@pytest.fixture()
def agent(monkeypatch):
    """The email assistant in a held run, with the switch on."""
    from acb_skills import ask_tools
    from acb_skills.write_artifact import bind_artifact_context

    F.flag_on(monkeypatch)
    mod = F.load_email_agent("ea_held_no_ticked")
    bind_artifact_context(no_egress=True, agent_name="email-assistant", session_id="s-1",
                          member=F.MEMBER)
    posts: list[tuple[str, dict[str, Any]]] = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        posts.append((path, body))
        if path == "/email/held-card":
            return {"card_id": "e" * 32, "title": "Send this draft?", "detail": "d",
                    "context": "c", "rows": [
                        {"id": "to-0", "label": f"To: {F.KNOWN}", "checked": True,
                         "kind": "to", "class": "sent"},
                        {"id": "cc-0", "label": f"Cc: {F.STRANGER}", "checked": False,
                         "kind": "cc", "class": "new"}]}
        if path == "/email/held-card/act":
            raise mod.GatewayError("Email POST /email/held-card/act failed (422)", 422,
                                   detail="Not sent: no recipient was ticked.")
        raise AssertionError(path)

    async def card(**kw: Any) -> Any:
        posts.append(("card", kw))
        return ask_tools.RowsAnswer(kw["request_id"], 'APPROVE {"rows":["cc-0"]}',
                                    frozenset({"cc-0"}), F.MEMBER, "chat-thread-1")

    monkeypatch.setattr(mod, "_post", post)
    monkeypatch.setattr(mod, "_current_user_email", lambda: F.MEMBER)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "reply_card_channel_open", lambda: True)
    return mod, posts


async def test_the_tool_says_not_sent(agent) -> None:
    mod, posts = agent
    out = await mod.send_draft(F.BOX, F.DRAFT)
    assert out == "Not sent: no recipient was ticked."
    # The card showed the gateway's rows, with the gateway's card id.
    shown = next(kw for p, kw in posts if p == "card")
    assert shown["request_id"] == "e" * 32 and shown["with_answer"] is True
    assert [r["id"] for r in shown["rows"]] == ["to-0", "cc-0"]
    assert [p for p, _ in posts] == ["/email/held-card", "card", "/email/held-card/act"]
