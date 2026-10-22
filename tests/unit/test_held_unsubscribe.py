"""An unsubscribe in a held chat (``email_app_master_plan.md`` §21.3).

The link comes only from the stored ``List-Unsubscribe`` header of the
sender's mail. The card names the sender and the host. The record holds the
sender, the host and a hash of the link. The act reads the link again, and
acts only when the host and the hash still match. Open question 7 took the
safer choice: a held chat takes an https one-click link only.

R7, each with the mutation that turns it red:

* ``test_the_link_comes_from_the_stored_header`` — pass a link of the
  request to the unsubscribe.
* ``test_no_request_field_carries_a_link`` — drop ``extra="forbid"``.
* ``test_a_changed_host_or_link_sends_nothing`` — drop the host or the hash
  check of ``_act_unsubscribe``.
* ``test_a_mailto_target_sends_nothing`` and ``test_an_http_link_sends_nothing``
  — accept such a link.
* ``test_the_card_names_the_sender_and_the_host`` — drop the host from the card.

Redis, the database and the request are fakes. No request leaves.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.automation import held_card as hc
from gateway.routes.email.automation import senders
from pydantic import ValidationError

from tests.unit import _held_card_fakes as F

SENDER = "news@list.example"
LINK = "https://unsub.list.example/u?id=42"


@pytest.fixture()
def world(monkeypatch):
    F.flag_on(monkeypatch)
    monkeypatch.setattr(hc, "ANSWER_WAIT_S", 0.0)
    F.install_redis(monkeypatch)
    state: dict[str, Any] = {"link": LINK, "posts": [], "blocks": []}

    async def stored(account_id: str, owner: str, sender: str) -> tuple[str, str]:
        assert (account_id, owner, sender) == (F.BOX, F.MEMBER, SENDER)
        return senders._unsubscribe_kind(state["link"]), state["link"]

    async def unsubscribe(req: Any, background: Any, user: Any) -> dict[str, Any]:
        state["posts"].append(req)
        return {"ok": True, "archived": 3}

    async def block(req: Any, background: Any, user: Any) -> dict[str, Any]:
        state["blocks"].append(req)
        return {"ok": True, "archived": 2}

    monkeypatch.setattr(hc, "_stored_target", stored)
    monkeypatch.setattr(senders, "unsubscribe_sender", unsubscribe)
    monkeypatch.setattr(senders, "upsert_newsletter", block)
    return state


async def _card() -> dict[str, Any]:
    return await hc.held_card(
        hc.HeldCardRequest(kind="unsubscribe", account_id=F.BOX, email=SENDER), F.user())


async def _act(card: dict[str, Any], *ids: str) -> dict[str, Any]:
    answer = F.rows_answer(*(ids or (hc.UNSUBSCRIBE_ROW,)))
    await F.record_answer(card["card_id"], answer)
    return await hc.held_card_act(
        hc.HeldCardActRequest(card_id=card["card_id"], card_thread_id="chat-thread-1",
                              answer=answer), BackgroundTasks(), F.user())


async def test_the_card_names_the_sender_and_the_host(world) -> None:
    card = await _card()
    assert card["title"] == f"Unsubscribe from {SENDER}?"
    assert "unsub.list.example" in card["rows"][0]["label"]
    assert SENDER in card["rows"][0]["label"]
    assert "Host of the link: unsub.list.example" in card["context"]
    assert "List-Unsubscribe" in card["context"]


async def test_the_link_comes_from_the_stored_header(world) -> None:
    card = await _card()
    out = await _act(card)
    assert out["unsubscribed"] is True and out["host"] == "unsub.list.example"
    assert [r.unsubscribe_link for r in world["posts"]] == [LINK]


def test_no_request_field_carries_a_link() -> None:
    for extra in ({"unsubscribe_link": LINK}, {"link": LINK}, {"url": LINK}):
        with pytest.raises(ValidationError):
            hc.HeldCardRequest(kind="unsubscribe", account_id=F.BOX, email=SENDER, **extra)
        with pytest.raises(ValidationError):
            hc.HeldCardActRequest(card_id="a" * 32, card_thread_id="t", answer="x", **extra)


@pytest.mark.parametrize("changed", [
    "https://evil.example/u?id=42",                 # another host
    "https://unsub.list.example/u?id=42&d=secret",  # the same host, another link
])
async def test_a_changed_host_or_link_sends_nothing(world, changed: str) -> None:
    card = await _card()
    world["link"] = changed
    with pytest.raises(HTTPException) as e:
        await _act(card)
    assert e.value.status_code == 409 and e.value.detail == hc.LINK_CHANGED
    assert world["posts"] == [] and world["blocks"] == []


async def test_a_mailto_target_sends_nothing(world) -> None:
    world["link"] = "mailto:unsub@list.example?subject=stop"
    with pytest.raises(HTTPException) as e:
        await _card()
    assert e.value.status_code == 422 and e.value.detail == hc.MAILTO_REFUSED


async def test_an_http_link_sends_nothing(world) -> None:
    world["link"] = "http://unsub.list.example/u?id=42"
    with pytest.raises(HTTPException) as e:
        await _card()
    assert e.value.status_code == 422 and e.value.detail == hc.LINK_REFUSED


async def test_a_sender_with_no_link_is_blocked_inside_the_mailbox(world) -> None:
    world["link"] = ""
    card = await _card()
    assert card["title"] == f"Block {SENDER}?"
    out = await _act(card)
    assert out["action"] == "block" and world["posts"] == []
    assert [r.status for r in world["blocks"]] == ["AUTO_ARCHIVED"]


async def test_a_link_that_appears_after_a_block_card_sends_nothing(world) -> None:
    """The card offered a block. A new mail stored a link meanwhile. The act
    does not send a request that the card did not show."""
    world["link"] = ""
    card = await _card()
    world["link"] = LINK
    with pytest.raises(HTTPException) as e:
        await _act(card)
    assert e.value.status_code == 409
    assert world["posts"] == [] and world["blocks"] == []


async def test_an_unticked_card_changes_nothing(world) -> None:
    card = await _card()
    answer = F.rows_answer("bogus")
    await F.record_answer(card["card_id"], answer)
    with pytest.raises(HTTPException):
        await hc.held_card_act(
            hc.HeldCardActRequest(card_id=card["card_id"], card_thread_id="chat-thread-1",
                                  answer=answer), BackgroundTasks(), F.user())
    assert world["posts"] == [] and world["blocks"] == []


@pytest.fixture()
def agent(monkeypatch):
    from acb_skills import ask_tools
    from acb_skills.write_artifact import bind_artifact_context

    F.flag_on(monkeypatch)
    mod = F.load_email_agent("ea_held_unsub")
    bind_artifact_context(no_egress=True, agent_name="email-assistant", session_id="s-1",
                          member=F.MEMBER)
    posts: list[tuple[str, dict[str, Any]]] = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        posts.append((path, body))
        if path == "/email/held-card":
            return {"card_id": "f" * 32, "title": "Unsubscribe?", "detail": "d",
                    "context": "c", "rows": [{"id": "unsub-0", "label": "u",
                                              "kind": "unsubscribe", "checked": True}]}
        return {"ok": True, "action": "unsubscribe", "unsubscribed": True,
                "host": "unsub.list.example", "archived": 3}

    async def card(**kw: Any) -> Any:
        return ask_tools.RowsAnswer(kw["request_id"], 'APPROVE {"rows":["unsub-0"]}',
                                    frozenset({"unsub-0"}), F.MEMBER, "chat-thread-1")

    async def get(path: str, params: Any = None) -> Any:
        raise AssertionError(f"the held path reads no link itself: {path}")

    monkeypatch.setattr(mod, "_post", post)
    monkeypatch.setattr(mod, "_get", get)
    monkeypatch.setattr(mod, "_current_user_email", lambda: F.MEMBER)
    monkeypatch.setattr(ask_tools, "request_confirmation", card)
    monkeypatch.setattr(ask_tools, "reply_card_channel_open", lambda: True)
    return mod, posts


async def test_the_tool_posts_no_link(agent) -> None:
    mod, posts = agent
    out = await mod.unsubscribe_sender(F.BOX, SENDER, name="News")
    assert out.startswith(f"Unsubscribed from {SENDER} through host unsub.list.example")
    assert posts[0] == ("/email/held-card",
                        {"kind": "unsubscribe", "account_id": F.BOX, "email": SENDER})
    assert "link" not in str(posts)
