"""Email work through the one assistant, end to end (§7.1.1 rule 8).

D99 (``navigation_shell.md`` §7.1 and §7.1.1). The member talks to
``orchestrator`` only, and email work runs in ``email-assistant`` by
delegation. This suite drives the REAL orchestrator run and the REAL
email-assistant factory through ``evals.routing.harness``. Only the model wire
and the email gateway are fakes. The held state of each sub-run is the real
one: the first hand-off of the chat marks the session, and each later
hand-off is held, with ``EMAIL_HELD_CARD_SEND`` on as in production.

R7 fences named here:

* ``one-assistant-email-cards-reach-parent``: a delegated reply card
  (``compose_reply``) and a delegated confirmation card (a rule) are events
  of the PARENT's stream, owned by the parent's thread, as
  ``test_delegated_confirmation_card.py`` proves for projects-assistant.
* ``one-assistant-email-five-turns``: one chat, five turns, off ``/email``,
  with two mailboxes:

  1. "what needs a reply" — the orchestrator's context lists both
     mailboxes, its task names none, and the read pools All inboxes (an empty account_id).
  2. "reply to Dr Sagar and cc Kiran" — ONE task. The reply card saves the
     draft, and edit_draft adds Kiran in Cc, as the real agent does (the
     card adds no Cc). The working set keeps the mail and the draft.
  3. "send it" — the email sub-run gets the draft_id of the working set as
     context, and sends that draft.
  4. "set a rule on my personal box" — the label resolves to Personal, the
     rule is made there, and the turn carries "Now working on Personal".
  5. "which mailbox needs reconnecting" — the orchestrator's context names
     Personal as needing it, and it answers with no hand-off.

Run::

    uv run pytest tests/unit/test_one_assistant_email.py -v
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from evals.routing import dataset as ds
from evals.routing.harness import (
    ORCHESTRATOR,
    ReplyModel,
    RoutingHarness,
    RunRecord,
    specialist_tool,
    text_turn,
    tool_turn,
)

EA = "email-assistant"
TOOL = specialist_tool(EA)
LIMIT = 60.0

WORK = "0a0a0a0a-0000-4000-8000-00000000000a"
HOME = "0b0b0b0b-0000-4000-8000-00000000000b"
BOXES = [
    {"id": WORK, "email_address": "vj@hathilabs.test", "label": "Outlook",
     "display_label": "Work", "provider": "microsoft", "is_default": True,
     "in_all_inboxes": True, "unread_count": 3},
    {"id": HOME, "email_address": "vjvarada@gmail.test", "label": "Gmail",
     "display_label": "Personal", "provider": "gmail", "in_all_inboxes": True,
     "needs_reconnect": True, "unread_count": 1},
]
MAIL = "e-sagar-1"
DRAFT = "d-sagar-9"
SAGAR = "sagar@clinic.test"
KIRAN = "kiran@hathilabs.test"
PROJECTS = "## Where the member is now\nThe member is in the Projects app (/projects)."


class Gateway:
    """The email routes the five turns reach. Each call is kept."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.puts: list[Any] = []

    async def get(self, path: str, params: dict | None = None) -> Any:
        self.calls.append(("GET", path, params))
        if path == "/email/accounts":
            return [dict(b) for b in BOXES]
        if path == "/email/messages":
            return {"total": 1, "emails": [{
                "id": MAIL, "account_id": WORK, "subject": "Quote for the clinic",
                "from_address": {"name": "Dr Sagar", "email": SAGAR}}]}
        if path.startswith("/email/rules"):
            return []
        if path == f"/email/messages/{DRAFT}":
            return {"id": DRAFT, "account_id": WORK, "folder": "drafts",
                    "subject": "Re: Quote for the clinic",
                    "to_addresses": [{"name": "Dr Sagar", "email": SAGAR}],
                    "cc_addresses": [] if not self.puts else [{"email": KIRAN}]}
        if path == f"/email/messages/{DRAFT}/full-body":
            return {"body_text": "Dear Dr Sagar,\n\nThank you. We accept.\n\nVJ"}
        if path == "/email/drafts/scheduled":
            return []
        return {}

    async def post(self, path: str, body: Any) -> Any:
        self.calls.append(("POST", path, body))
        if path == "/email/reply-card":
            return {"message_id": MAIL, "mailbox": "Work · vj@hathilabs.test",
                    "subject": "Re: Quote for the clinic", "draft_id": DRAFT,
                    "body": "Dear Dr Sagar,\n\nThank you. We accept.\n\nVJ",
                    "recipients": {"reply": {"to": [SAGAR], "cc": []},
                                   "reply_all": {"to": [SAGAR], "cc": []}},
                    "classes": {SAGAR: "known"}}
        if path == "/email/reply-card/act":
            return {"action": "save", "draft_id": DRAFT, "to": [SAGAR], "cc": [],
                    "subject": "Re: Quote for the clinic",
                    "mailbox": "Work · vj@hathilabs.test"}
        if path == "/email/held-card":
            return {"title": "Send this draft?", "detail": f"To {SAGAR}, Cc {KIRAN}",
                    "card_id": "c0ffee00c0ffee00c0ffee00c0ffee01",
                    "rows": [{"id": "r-to", "label": SAGAR, "checked": True},
                             {"id": "r-cc", "label": KIRAN, "checked": True}]}
        if path == "/email/held-card/act":
            return {"to": [SAGAR], "cc": [KIRAN]}
        if path == "/email/rules":
            return {"id": "rule-1", "name": body.get("name"), "enabled": True}
        return {}

    async def patch(self, path: str, body: Any) -> Any:
        self.calls.append(("PATCH", path, body))
        return {}

    async def put(self, path: str, body: Any) -> Any:
        self.calls.append(("PUT", path, body))
        self.puts.append(body)
        return {}

    def posts(self, path: str) -> list[Any]:
        return [b for m, p, b in self.calls if m == "POST" and p == path]


def _reply_answer(value: dict[str, Any]) -> str:
    """The member's Save on a reply card, as the browser posts it
    (``reply_card.parse_reply_answer``). The card adds no Cc: ``cc`` is the
    list the member saw, and the gateway derives the recipients."""
    form = value["reply"]
    return "APPROVE " + json.dumps({"reply": {
        "action": "save", "reply_all": bool(form.get("reply_all")),
        "body": form.get("body") or "Thanks", "to": [SAGAR], "cc": [],
        "message_id": form.get("message_id") or MAIL,
    }})


def _answer(_record: RunRecord, value: dict[str, Any]) -> str:
    if value.get("reply"):
        return _reply_answer(value)
    rows = value.get("rows")
    if rows:
        return "APPROVE " + json.dumps({"rows": [r["id"] for r in rows]})
    return "APPROVE"


def _sub_tools(record: RunRecord) -> list[tuple[str, dict]]:
    starts = [e for e in record.events if e.get("type") == "SUB_AGENT_TOOL_CALL_START"]
    return [(e["toolCallName"], json.loads(e.get("args") or "{}")) for e in starts]


def _sub_results(record: RunRecord) -> list[str]:
    return [str(e.get("content") or "") for e in record.events
            if e.get("type") == "SUB_AGENT_TOOL_CALL_RESULT"]


def _orch_tasks(record: RunRecord) -> list[str]:
    return [json.loads(a or "{}").get("task", "") for n, a in record.tool_calls if n == TOOL]


def _last_body_text(model: ReplyModel) -> str:
    return json.dumps(model.bodies[-1].get("messages") or [], ensure_ascii=False)


@pytest.fixture
def chat(monkeypatch: pytest.MonkeyPatch):
    from acb_common import get_settings

    from tests.unit._native_maf_harness import load_agent_module

    monkeypatch.setattr(get_settings(), "email_held_card_send", True, raising=False)
    gw = Gateway()
    with RoutingHarness() as harness:
        harness.mailboxes = BOXES
        mod = load_agent_module(harness._registry[EA]["local_path"])
        harness._modules[EA] = mod
        monkeypatch.setattr(mod, "_get", gw.get)
        monkeypatch.setattr(mod, "_post", gw.post)
        monkeypatch.setattr(mod, "_patch", gw.patch)
        monkeypatch.setattr(mod, "_put", gw.put)
        yield harness, gw


def _turn(harness: RoutingHarness, words: str, thread: str | None = None) -> RunRecord:
    record = asyncio.run(harness.drive(
        words, PROJECTS, member=ds.MEMBER, org=ds.ORG, on_card=_answer,
        timeout=LIMIT, thread_id=thread,
    ))
    assert record.error is None, record.error
    return record


def test_the_five_utterances_in_one_chat(chat) -> None:
    harness, gw = chat
    orch = ReplyModel([
        tool_turn(TOOL, {"task": "What needs a reply? The member named no mailbox."}),
        text_turn("Dr Sagar needs a reply about the quote."),
        tool_turn(TOOL, {"task": (f"Reply to [Quote for the clinic](/email?email={MAIL}), "
                                  f"email_id {MAIL}, and Cc {KIRAN}.")}),
        text_turn("Saved in Drafts with Kiran in Cc."),
        tool_turn(TOOL, {"task": f"Send the draft. draft_id {DRAFT}."}),
        text_turn("Sent."),
        tool_turn(TOOL, {"task": "Set a rule on my personal box: label receipts as Receipts."}),
        text_turn("The rule is in Personal."),
        text_turn("Personal needs reconnecting. Sign in again from the Email app's mailbox menu."),
    ])
    email = ReplyModel([
        tool_turn("query_inbox", {"account_id": "", "unread_only": True}),
        text_turn(f"Dr Sagar asks for a reply: id={MAIL}."),
        tool_turn("compose_reply", {"email_id": MAIL, "account_id": WORK}),
        # The real path after the card (compose_reply's docstring): the card
        # adds no Cc, so the agent adds Kiran to the saved draft.
        tool_turn("edit_draft", {"account_id": WORK, "draft_id": DRAFT, "cc": [KIRAN]},
                  call_id="call_2"),
        text_turn("Saved in Drafts with Kiran in Cc."),
        tool_turn("send_draft", {"account_id": WORK, "draft_id": DRAFT}),
        text_turn("Draft sent."),
        tool_turn("create_rule", {"account_id": "my personal box", "name": "Receipts",
                                  "subject_pattern": "receipt", "label": "Receipts"}),
        text_turn("Rule created in Personal."),
    ])
    harness.models = {ORCHESTRATOR: orch, EA: email}

    # 1. "what needs a reply": off /email, two mailboxes, no mailbox named.
    one = _turn(harness, "what needs a reply")
    thread = one.thread_id
    first = json.dumps(orch.bodies[0].get("messages") or [], ensure_ascii=False)
    assert "The member's mailboxes" in first
    assert f"(account_id: {WORK})" in first and f"(account_id: {HOME})" in first
    assert _orch_tasks(one) == ["What needs a reply? The member named no mailbox."]
    assert _sub_tools(one) == [("query_inbox", {"account_id": "", "unread_only": True})]
    assert any(m == "GET" and path == "/email/messages" for m, path, _ in gw.calls), (
        gw.calls, _sub_results(one))
    assert not any((p or {}).get("account_id") for m, path, p in gw.calls
                   if m == "GET" and path == "/email/messages"), "All inboxes reads pool"

    # 2. "reply to Dr Sagar and cc Kiran": ONE task, the reply card, the Cc.
    two = _turn(harness, "reply to Dr Sagar and cc Kiran", thread)
    tasks = _orch_tasks(two)
    assert len(tasks) == 1 and MAIL in tasks[0] and KIRAN in tasks[0], tasks
    assert _sub_tools(two) == [
        ("compose_reply", {"email_id": MAIL, "account_id": WORK}),
        ("edit_draft", {"account_id": WORK, "draft_id": DRAFT, "cc": [KIRAN]}),
    ]
    assert len(two.cards) == 2, two.cards  # the reply card, then the edit card
    assert all(c["owner"] == thread and c["answered"] for c in two.cards), two.cards
    (act,) = gw.posts("/email/reply-card/act")
    assert json.loads(act["answer"][len("APPROVE "):])["reply"]["cc"] == []
    (put,) = gw.puts
    assert put["draft_id"] == DRAFT and put["cc"] == [KIRAN] and put["account_id"] == WORK
    assert any(f"draft_id={DRAFT}" in r for r in _sub_results(two)), _sub_results(two)

    # 3. "send it": the sub-run gets the working set as context.
    three = _turn(harness, "send it", thread)
    sent_to_email = _last_body_text(email)
    assert "Working set from this chat" in sent_to_email
    assert f"draft_id: {DRAFT}" in sent_to_email and f"email_id: {MAIL}" in sent_to_email
    assert "«Work»" in sent_to_email
    orch_ctx = json.dumps(orch.bodies[4].get("messages") or [], ensure_ascii=False)
    assert "Email working set of this chat" in orch_ctx and DRAFT in orch_ctx
    assert _sub_tools(three) == [("send_draft", {"account_id": WORK, "draft_id": DRAFT})]
    (held,) = gw.posts("/email/held-card")
    assert held == {"kind": "draft", "account_id": WORK, "draft_id": DRAFT}
    assert gw.posts("/email/held-card/act"), (three.cards, _sub_results(three))
    assert any(r.startswith("Draft sent") for r in _sub_results(three)), _sub_results(three)

    # 4. "set a rule on my personal box": the label resolves, the mark draws.
    four = _turn(harness, "set a rule on my personal box", thread)
    (rule,) = gw.posts("/email/rules")
    assert rule["account_id"] == HOME, rule
    marks = [e for e in four.events
             if e.get("type") == "CUSTOM" and e.get("name") == "email_mailbox_mark"]
    assert [m["value"] for m in marks] == [{"account_id": HOME, "label": "Personal"}], marks
    assert all(c["owner"] == thread for c in four.cards), four.cards

    # 5. "which mailbox needs reconnecting": answered from the context.
    five = _turn(harness, "which mailbox needs reconnecting", thread)
    assert _orch_tasks(five) == [] and _sub_tools(five) == []
    ctx = _last_body_text(orch)
    assert f"«Personal» «vjvarada@gmail.test» (account_id: {HOME}), in All inboxes, NEEDS RECONNECTING" in ctx
    assert "Personal needs reconnecting" in five.answer
    # Each turn ran in the chat's own thread, and nothing else left the box.
    assert harness.sent == []


def test_a_delegated_reply_card_reaches_the_parent(chat) -> None:
    """The first hand-off of a chat: not held. The reply card is the
    parent's, and the steps and their results reach the parent's stream."""
    harness, gw = chat
    harness.models = {
        ORCHESTRATOR: ReplyModel([
            tool_turn(TOOL, {"task": f"Reply to email_id {MAIL}."}),
            text_turn("Saved."),
        ]),
        EA: ReplyModel([
            tool_turn("compose_reply", {"email_id": MAIL, "account_id": WORK}),
            text_turn("Saved in Drafts."),
        ]),
    }
    record = _turn(harness, "reply to Dr Sagar")
    (card,) = record.cards
    assert card["owner"] == record.thread_id and card["answered"] is True
    ev = record.events
    at = next(i for i, e in enumerate(ev) if e.get("type") == "CUSTOM"
              and e.get("name") == "confirmation_requested")
    assert ev[at]["value"].get("reply", {}).get("message_id") == MAIL
    step = next(i for i, e in enumerate(ev) if e.get("type") == "SUB_AGENT_TOOL_CALL_START")
    done = next(i for i, e in enumerate(ev) if e.get("type") == "RUN_FINISHED")
    assert at < step < done
    relayed = [tid for tid, line in harness.relayed if card["request_id"] in line]
    assert relayed and set(relayed) == {record.thread_id}, relayed
    assert gw.posts("/email/reply-card/act")


def test_a_delegated_confirmation_card_reaches_the_parent(chat) -> None:
    harness, gw = chat
    harness.models = {
        ORCHESTRATOR: ReplyModel([
            tool_turn(TOOL, {"task": "Set a rule in Personal: label receipts."}),
            text_turn("Done."),
        ]),
        EA: ReplyModel([
            tool_turn("create_rule", {"account_id": "Personal", "name": "Receipts",
                                      "subject_pattern": "receipt", "label": "Receipts"}),
            text_turn("Rule created."),
        ]),
    }
    record = _turn(harness, "set a rule on my personal box")
    (card,) = record.cards
    assert card["owner"] == record.thread_id and card["answered"] is True, card
    (rule,) = gw.posts("/email/rules")
    assert rule["account_id"] == HOME
