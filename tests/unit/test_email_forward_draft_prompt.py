"""A forward drafts a note to the people it goes to, never to the original sender.

Owner report, 2026-11-06 (task 1817): the member forwarded a customer's email
to the team, and "Draft with AI" wrote to the customer.

Each rule names the mutation that turns it red (R7):

1. **The forward prompt names the readers and says the note is to them.**
   Mutation: drop the forward lines from the prompt ->
   ``test_a_forward_with_to_writes_to_the_people_it_goes_to``.
2. **The original sender is only "forwarded from", and the model must not
   answer the forwarded email.** Mutation: use the reply rule for a forward ->
   ``test_a_forward_with_to_writes_to_the_people_it_goes_to``.
3. **With no To, the reader has no name, and it is never the sender.**
   Mutation: put the sender in ``Recipient`` ->
   ``test_a_forward_without_to_writes_to_an_unnamed_reader``.
4. **A reply keeps its prompt.** Mutation: use the forward rule for a reply ->
   ``test_a_reply_keeps_its_prompt``.
5. **The request carries the Cc, and the forward step passes To, Cc and the
   sender.** Mutation: drop ``cc`` from the request ->
   ``test_the_request_carries_the_cc`` and ``test_the_forward_step_*``.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gateway.routes.email.automation import drafting as dr

CUSTOMER = "Ravi Kumar <ravi@customer.test>"
FORWARDED = "Ravi's mail: please quote three BQs by Friday."


def _resp(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


async def _prompt(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> tuple[str, str]:
    """Run ``_llm_compose_assist`` with a fake model. Return (system, user)."""
    seen: list[list[dict[str, str]]] = []

    async def fake(*, model, messages, **_kw):
        seen.append(messages)
        return _resp("Hello,\n\nA note."), model

    monkeypatch.setattr("acb_llm.context.acompletion_with_fallback", fake)
    args: dict[str, Any] = dict(
        about="", signature="", current_body="", instruction="tell them to quote",
        subject="BQ quote", reply_to_body=FORWARDED, model="tier-fast")
    args.update(kw)
    await dr._llm_compose_assist(**args)
    [messages] = seen
    return messages[0]["content"], messages[1]["content"]


async def test_a_forward_with_to_writes_to_the_people_it_goes_to(monkeypatch) -> None:
    system, user = await _prompt(
        monkeypatch, mode="forward", recipient="Team <team@us.test>",
        copied="boss@us.test", forwarded_from=CUSTOMER)
    assert "This email is a forward." in user
    assert "forwards this email to Team <team@us.test> (copied: boss@us.test)" in user
    assert "the note is to them" in user
    # The sender is named only as the place it is forwarded from.
    assert f"forwarded from {CUSTOMER}" in user
    assert f"Recipient: {CUSTOMER}" not in user
    # The model must not answer the forwarded email.
    assert "never answer the forwarded email as if you reply to its sender" in system
    assert "ADDRESS" not in system
    assert "The email you are replying to" not in user
    # The forwarded text stays data, inside the frame marks.
    assert "FORWARDED EMAIL" in user
    start = user.index("<<<FORWARDED EMAIL")
    end = user.index("<<<END FORWARDED EMAIL")
    assert start < user.index(FORWARDED) < end


async def test_a_forward_without_to_writes_to_an_unnamed_reader(monkeypatch) -> None:
    system, user = await _prompt(monkeypatch, mode="forward", forwarded_from=CUSTOMER)
    assert "has not named who gets the forward yet" in user
    assert "unnamed reader" in user
    assert "Do not write to the sender of the forwarded email" in user
    assert "Recipient:" not in user
    assert "forwards this email to" not in user
    assert "never answer the forwarded email" in system


async def test_a_reply_keeps_its_prompt(monkeypatch) -> None:
    system, user = await _prompt(monkeypatch, mode="reply", recipient=CUSTOMER)
    assert "This email is a reply." in user
    assert f"Recipient: {CUSTOMER}" in user
    assert "The email you are replying to — read it in full" in user
    assert "REPLIED EMAIL" in user and "FORWARD" not in user
    assert dr._REPLY_RULE in system
    assert "The email being replied to (if shown) is what you must ADDRESS." in system
    assert "never answer the forwarded email" not in system


async def test_a_new_mail_keeps_its_prompt(monkeypatch) -> None:
    system, user = await _prompt(
        monkeypatch, mode="new", recipient="Team <team@us.test>", reply_to_body="")
    assert "Recipient: Team <team@us.test>" in user
    assert "This email is a" not in user
    assert dr._REPLY_RULE in system


def test_the_request_carries_the_cc() -> None:
    req = dr.ComposeAssistRequest(
        account_id="a1", mode="forward", to=["t@us.test"], cc=["c@us.test"])
    assert req.cc == ["c@us.test"]
    assert dr.ComposeAssistRequest(account_id="a1").cc is None


async def _step(monkeypatch: pytest.MonkeyPatch, req: dr.ComposeAssistRequest):
    """Run ``_compose_draft`` with ``_llm_compose_assist`` replaced. Return its kwargs."""
    got: dict[str, Any] = {}

    async def fake(**kw):
        got.update(kw)
        return "Hello,\n\nA note."

    monkeypatch.setattr(dr, "_llm_compose_assist", fake)
    ctx = {"from_name": "Ravi Kumar", "from": "ravi@customer.test",
           "subject": "BQ quote", "thread": "", "body": FORWARDED}
    await dr._compose_draft(
        req, "me@us.test", ctx, "", "", sender="", model="tier-fast",
        on_activity=None, on_delta=None)
    return got


async def test_the_forward_step_writes_to_the_to_and_names_the_sender_as_origin(monkeypatch) -> None:
    got = await _step(monkeypatch, dr.ComposeAssistRequest(
        account_id="a1", mode="forward", message_id="m1", body="",
        to=["team@us.test", "ops@us.test"], cc=["boss@us.test"]))
    assert got["recipient"] == "team@us.test, ops@us.test"
    assert got["copied"] == "boss@us.test"
    assert got["forwarded_from"] == CUSTOMER
    assert "ravi@customer.test" not in got["recipient"]


async def test_the_forward_step_with_no_to_has_no_recipient(monkeypatch) -> None:
    got = await _step(monkeypatch, dr.ComposeAssistRequest(
        account_id="a1", mode="forward", message_id="m1", body="", to=[]))
    assert got["recipient"] == ""
    assert got["forwarded_from"] == CUSTOMER


async def test_the_reply_step_still_writes_to_the_sender(monkeypatch) -> None:
    # A reply with the member's own words routes to the improve path.
    got = await _step(monkeypatch, dr.ComposeAssistRequest(
        account_id="a1", mode="reply", message_id="m1", body="My words.",
        to=["other@x.test"], cc=["c@x.test"]))
    assert got["recipient"] == CUSTOMER
    assert got["copied"] == ""
    assert got["forwarded_from"] == ""
