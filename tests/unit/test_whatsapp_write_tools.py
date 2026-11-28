"""P-4b (task 3961): the seven WhatsApp write tools of the chat, hermetic.

Spec: ``project-docs/specs/chat_parity.md`` §6. Module:
``apps/agents/agent-whatsapp-assistant/agents.py``.

A fake gateway answers each route over ``httpx.MockTransport``, and records
each request. The card is the REAL ``ask_tools.request_confirmation`` on a run
queue, so each answer takes the path of production. What is locked, each with
its mutation (R7):

1. Each tool shows ONE card with its title and its text, and an approve sends
   ONE write with the route and the body of the page. Mutation: send before the
   card -> red.
2. A deny, a timeout, a card that a message closed, an answer of another member,
   ``refuse_cards``, a run with no person and a run with no card channel send no
   write. Mutation: read any truthy answer as an approve -> red.
3. A bad argument, an unknown id and a run with no member make no card and no
   write. Mutation: drop ``_uuid_arg`` -> red.
4. A 4xx says that nothing changed. A 5xx and a lost connection say that it may
   have changed. No tool sends a write twice. Mutation: answer success for any
   status -> red.
5. A marked chat and a ``no_egress`` run hold off the seven writes and keep
   each other tool. A WhatsApp read marks no chat (task 4010). Mutation: drop
   ``HELD_OFF_OWN_WRITES`` from ``is_held_off_write`` -> red.
6. The copies of the gateway rules stay equal to the gateway. Mutation: change a
   default category on one side -> red.
7. Each card states the real effect (review of cf48ec6e2): one line for each
   name, no warning on a group snooze (task 4011 is fixed), the wake promise of a
   Cloud number only, the workflow line of a new task, a second read after
   Approve, and no wake card from a full snoozed list.

Run: ``uv run pytest tests/unit/test_whatsapp_write_tools.py -q``.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from acb_common.no_person import no_person_scope
from acb_skills import ask_tools, egress
from acb_skills import chat_parity as parity
from orchestrator import _tool_injection as ti
from orchestrator import executor

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO / "apps" / "agents" / "agent-whatsapp-assistant"
OWNER = "whatsapp-assistant"
MEMBER = "founder@example.test"
_REAL_CLIENT = httpx.AsyncClient


def _id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


ACC, ACC2, REPLY, CAT, CHAT, SNOOZED, MSG = (_id(n) for n in range(1, 8))
STRANGER = _id(99)

WRITES = (
    "create_whatsapp_saved_reply", "delete_whatsapp_saved_reply",
    "update_whatsapp_category", "seed_whatsapp_categories",
    "snooze_whatsapp_chat", "unsnooze_whatsapp_chat", "capture_whatsapp_task",
)


@pytest.fixture(scope="module")
def wa():
    spec = importlib.util.spec_from_file_location("wa_writes", AGENT_DIR / "agents.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Gateway:
    """The WhatsApp routes of one member with two numbers."""

    def __init__(self) -> None:
        self.accounts = [
            {"id": ACC2, "display_name": "Second", "phone_number": "+91 2", "is_default": False,
             "provider": "whatsmeow"},
            {"id": ACC, "display_name": "Shop", "phone_number": "+91 1", "is_default": True,
             "provider": "cloud_api"},
        ]
        #: chat -> its number. A chat with no row here is on ``ACC``.
        self.chat_account: dict[str, str] = {}
        self.replies = {ACC: [{"id": REPLY, "title": "Price list", "body": "Rate is 40.\nAsk us.",
                               "shortcut": "/price"}], ACC2: []}
        self.categories = {ACC: [{"id": CAT, "name": "VIP", "notify_policy": "instant",
                                  "auto_reply_policy": "never", "draft_policy": "always"}],
                           ACC2: []}
        self.messages = {CHAT: [{"id": MSG, "chat_id": CHAT, "direction": "in",
                                 "sender_name": "Ravi", "chat_name": "Ravi Traders",
                                 "chat_kind": "dm", "body_text": "Send the quote by Friday",
                                 "sent_at": "2026-11-20T10:00:00+00:00"}]}
        self.snoozed = [{"id": SNOOZED, "name": "Dealers North", "wa_chat_id": "g1",
                         "snoozed_until": "2030-01-01T00:00:00+00:00"}]
        self.contact = {"display_name": "Anil", "phone_number": "+91 3"}
        self.captured = True
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []
        self.forced: dict[str, tuple[int, Any]] = {}
        self.lost: set[str] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        query = dict(request.url.params)
        body = json.loads(request.content) if request.content else None
        self.calls.append((method, path, query, body))
        assert request.headers["x-user-email"] == MEMBER
        if method in self.lost:
            raise httpx.ReadTimeout("lost", request=request)
        if method in self.forced:
            status, answer = self.forced[method]
            return httpx.Response(status, json=answer)
        if method == "GET":
            return self._read(path, query)
        if path == "/whatsapp/saved-replies":
            return httpx.Response(201, json={"id": _id(50), **body})
        if method == "DELETE":
            return httpx.Response(204)
        if method == "PATCH":
            return httpx.Response(200, json={"id": CAT, "name": "VIP", **body})
        if path.endswith("/categories/bootstrap"):
            names = [c[0] for c in _DEFAULT_NAMES]
            return httpx.Response(200, json=[{"name": n} for n in names])
        if path.endswith(("/snooze", "/unsnooze")):
            return httpx.Response(200, json={"chat_id": path.split("/")[3],
                                             "snoozed_until": (body or {}).get("until")})
        assert path == "/whatsapp/capture-task", path
        return httpx.Response(201, json={"item_id": "t-1", "title": body["title"],
                                         "created": self.captured})

    def _read(self, path: str, query: dict[str, str]) -> httpx.Response:
        if path == "/whatsapp/accounts":
            return httpx.Response(200, json=self.accounts)
        if path == "/whatsapp/saved-replies":
            return httpx.Response(200, json=self.replies[query["account_id"]])
        if path == "/whatsapp/categories":
            return httpx.Response(200, json=self.categories[query["account_id"]])
        if path == "/whatsapp/search":
            owner = self.chat_account.get(query["chat_id"], ACC)
            rows = self.messages.get(query["chat_id"], [])
            return httpx.Response(
                200, json=rows[:1] if query["account_id"] == owner else [])
        if path == "/whatsapp/chats":
            assert query == {"stream": "snoozed", "limit": "200"}
            return httpx.Response(200, json=self.snoozed)
        if path.endswith("/context"):
            return httpx.Response(200, json={"contact": self.contact})
        if path.endswith("/messages"):
            chat = path.split("/")[3]
            rows = [m for m in self.messages.get(chat, []) if m["id"] == query["around"]]
            if not rows:
                return httpx.Response(404, json={"detail": "Message not found"})
            return httpx.Response(200, json=rows)
        raise AssertionError(path)

    def writes(self) -> list[tuple[str, str, Any]]:
        return [(m, p, b) for m, p, _q, b in self.calls if m != "GET"]


_DEFAULT_NAMES = (("VIP",), ("New customer",), ("Pending payment",), ("Dealer groups",),
                  ("Family & personal",), ("Noise",))


@pytest.fixture
def gw(monkeypatch, wa) -> _Gateway:
    g = _Gateway()
    transport = httpx.MockTransport(g.handle)
    monkeypatch.setattr(wa.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=transport, **kw))
    monkeypatch.setattr(wa, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(wa, "internal_auth_headers",
                        lambda: {"Authorization": "Bearer SECRET-TOKEN"})
    return g


def _approve(rid: str) -> dict[str, Any]:
    return {"answer": "APPROVE", "responder": MEMBER}


async def _with_card(
    call: Callable[[], Any], answer: Callable[[str], dict[str, Any]] = _approve,
) -> tuple[str, list[dict[str, Any]]]:
    """Run a tool in a run with a card channel. Answer each card with
    *answer*. Give the tool's text and each card that showed."""
    queue: asyncio.Queue = asyncio.Queue()
    token = executor._active_run_queue.set(queue)
    cards: list[dict[str, Any]] = []
    try:
        task = asyncio.create_task(call())
        while not task.done():
            take = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({take, task}, timeout=5,
                                         return_when=asyncio.FIRST_COMPLETED)
            assert done, "the tool hung"
            if take not in done:
                take.cancel()
                continue
            event = take.result()
            if event.get("name") != "confirmation_requested":
                continue
            cards.append(event["value"])
            rid = event["value"]["request_id"]
            result = answer(rid)
            if result is not None:
                executor._pending_user_input[rid].set_result(result)
        return await task, cards
    finally:
        executor._active_run_queue.reset(token)


def _future(days: int = 2) -> datetime:
    return (datetime.now(UTC) + timedelta(days=days)).replace(microsecond=0)


UNTIL = _future().astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()

#: tool -> the call, the one write, the card title and words of the card.
CASES: dict[str, tuple[Callable[[Any], Any], tuple[str, str, Any], str, tuple[str, ...]]] = {
    "create_whatsapp_saved_reply": (
        lambda wa: wa.create_whatsapp_saved_reply(
            "GST number", "Our GST is 29ABC.\nThank you.", "GST No"),
        ("POST", "/whatsapp/saved-replies", {
            "account_id": ACC, "title": "GST number",
            "body": "Our GST is 29ABC.\nThank you.", "shortcut": "/gstno"}),
        "Add this saved reply?",
        ("Number: Shop (+91 1)", "Title: GST number", "Shortcut: /gstno",
         "The full text:\nOur GST is 29ABC.\nThank you.", "It sends no message."),
    ),
    "delete_whatsapp_saved_reply": (
        lambda wa: wa.delete_whatsapp_saved_reply(REPLY),
        ("DELETE", f"/whatsapp/saved-replies/{REPLY}", None),
        "Delete this saved reply?",
        ('You lose the saved reply "Price list"', "There is no undo.", "Shortcut: /price",
         "The full text that you lose:\nRate is 40.\nAsk us."),
    ),
    "update_whatsapp_category": (
        lambda wa: wa.update_whatsapp_category(
            CAT, notify_policy="Never", draft_policy="always"),
        ("PATCH", f"/whatsapp/categories/{CAT}", {"notify_policy": "never"}),
        "Change this chat category?",
        ("Category: VIP", "Notify: instant becomes never", "It sends no message.",
         "Today only the rule preview reads these policies."),
    ),
    "seed_whatsapp_categories": (
        lambda wa: wa.seed_whatsapp_categories(),
        ("POST", f"/whatsapp/accounts/{ACC}/categories/bootstrap", {}),
        "Add the default chat categories?",
        ("Adds 5 categories:",
         "- New customer: notify digest, auto-reply holding, AI drafts on_intent, escalate 24h",
         "Categories that the number has now: 1. They stay as they are.",
         "permit an automatic reply with no card"),
    ),
    "snooze_whatsapp_chat": (
        lambda wa: wa.snooze_whatsapp_chat(CHAT, UNTIL),
        ("POST", f"/whatsapp/chats/{CHAT}/snooze", {
            "until": datetime.fromisoformat(UNTIL).astimezone(UTC).isoformat()}),
        "Snooze this chat?",
        ('Hides "Ravi Traders"', "Chat: Ravi Traders", f"Wakes at: {UNTIL.replace('T', ' ')}",
         "A new message from them wakes the chat sooner. Nothing is sent to them."),
    ),
    "unsnooze_whatsapp_chat": (
        lambda wa: wa.unsnooze_whatsapp_chat(SNOOZED),
        ("POST", f"/whatsapp/chats/{SNOOZED}/unsnooze", {}),
        "Wake this chat now?",
        ('Ends the snooze of "Dealers North"', "Snoozed until: 2030-01-01T00:00:00+00:00"),
    ),
    "capture_whatsapp_task": (
        lambda wa: wa.capture_whatsapp_task(CHAT, MSG),
        ("POST", "/whatsapp/capture-task", {
            "message_id": MSG, "title": "WhatsApp · Ravi: Send the quote by Friday"}),
        "Make a task from this message?",
        ("Chat: Ravi Traders", "From: Ravi",
         "Task title: WhatsApp · Ravi: Send the quote by Friday",
         "A new task can start the workflows of your organization.",
         "Send the quote by Friday", "This tool sends no message."),
    ),
}

_SUCCESS = ("Added ", "Deleted ", "Changed ", "Snoozed ", "Woke ")


def _claims_success(out: str) -> bool:
    return out.startswith(_SUCCESS)


# ── 1. One card, one write ───────────────────────────────────────────────────


def test_each_write_tool_has_a_case() -> None:
    assert set(CASES) == set(WRITES)


@pytest.mark.parametrize("name", WRITES)
async def test_an_approve_sends_one_write_after_one_card(gw, wa, name: str) -> None:
    call, write, title, words = CASES[name]
    sent_before_the_answer: list[int] = []

    def answer(rid: str) -> dict[str, Any]:
        sent_before_the_answer.append(len(gw.writes()))
        return _approve(rid)

    out, cards = await _with_card(lambda: call(wa), answer)
    assert len(cards) == 1, cards
    assert sent_before_the_answer == [0]
    assert cards[0]["title"] == title
    text = cards[0]["detail"] + "\n" + cards[0]["context"]
    for word in words:
        assert word in text, (word, text)
    assert gw.writes() == [write]
    assert _claims_success(out), out
    assert "SECRET-TOKEN" not in out + json.dumps(cards)


# ── 2. Each answer that is no approve sends nothing ──────────────────────────


def _reject(rid: str) -> dict[str, Any]:
    return {"answer": "REJECT", "responder": MEMBER}


def _closed(rid: str) -> dict[str, Any]:
    return {"answer": "make it Monday", "revised": True}


def _other(rid: str) -> dict[str, Any]:
    return {"answer": "APPROVE", "responder": "other@example.test"}


def _nobody(rid: str) -> dict[str, Any]:
    return {"answer": "APPROVE"}


@pytest.mark.parametrize(("answer", "lead"), [
    (_reject, "Declined"), (_closed, "Closed by your message"),
    (_other, "Answered by another member"), (_nobody, "Answered by another member"),
], ids=["deny", "closed", "other-member", "no-responder"])
@pytest.mark.parametrize("name", WRITES)
async def test_a_card_with_no_approve_sends_nothing(gw, wa, name, answer, lead) -> None:
    call = CASES[name][0]
    out, cards = await _with_card(lambda: call(wa), answer)
    assert len(cards) == 1
    assert gw.writes() == []
    assert out.startswith(lead), out
    assert not _claims_success(out)


@pytest.mark.parametrize("name", WRITES)
async def test_a_card_that_times_out_sends_nothing(gw, wa, name, monkeypatch) -> None:
    async def slow(*_a: Any, **_k: Any) -> Any:
        raise TimeoutError

    monkeypatch.setattr(executor, "wait_user_future", slow)
    out, cards = await _with_card(lambda: CASES[name][0](wa), lambda _rid: None)
    assert len(cards) == 1
    assert gw.writes() == []
    assert out.startswith("Timed out"), out


@pytest.mark.parametrize("scope", [ask_tools.refuse_cards, no_person_scope],
                         ids=["refuse-cards", "no-person"])
@pytest.mark.parametrize("name", WRITES)
async def test_a_run_that_cannot_ask_sends_nothing(gw, wa, name, scope) -> None:
    with scope():
        out, cards = await _with_card(lambda: CASES[name][0](wa))
    assert cards == []
    assert gw.writes() == []
    assert out.startswith("Not shown"), out


@pytest.mark.parametrize("name", WRITES)
async def test_a_run_with_no_card_channel_sends_nothing(gw, wa, name) -> None:
    executor._stream_relay_thread_id.set(None)
    out = await CASES[name][0](wa)
    assert gw.writes() == []
    assert not _claims_success(out), out


@pytest.mark.parametrize("who", ["", "room:42"], ids=["no-member", "shared-room"])
@pytest.mark.parametrize("name", WRITES)
async def test_a_run_with_no_member_makes_no_request(gw, wa, name, who, monkeypatch) -> None:
    monkeypatch.setattr(wa, "_current_user_email", lambda: who)
    out, cards = await _with_card(lambda: CASES[name][0](wa))
    assert cards == [] and gw.calls == []
    assert "nothing was changed" in out


async def test_the_card_is_bound_to_the_member_and_closes_on_a_message(
    gw, wa, monkeypatch,
) -> None:
    """Mutation: drop ``responder`` or ``close_on_steer`` -> red."""
    seen: list[dict[str, Any]] = []

    async def fake(title: str, detail: str = "", context: str = "", **kw: Any) -> bool:
        seen.append(kw)
        return False

    monkeypatch.setattr(ask_tools, "request_confirmation", fake)
    for name in WRITES:
        await CASES[name][0](wa)
    assert seen == [{"non_interactive_default": "deny", "close_on_steer": True,
                     "responder": MEMBER}] * len(WRITES)
    assert gw.writes() == []


# ── 3. A bad argument or an unknown id: no card, no write ────────────────────


_HIDDEN = "Pay here\u200bnow"

BAD: dict[str, Callable[[Any], Any]] = {
    "reply-no-title": lambda wa: wa.create_whatsapp_saved_reply("  ", "text"),
    "reply-no-text": lambda wa: wa.create_whatsapp_saved_reply("T", "  "),
    "reply-long-title": lambda wa: wa.create_whatsapp_saved_reply("t" * 81, "text"),
    "reply-long-text": lambda wa: wa.create_whatsapp_saved_reply("T", "b" * 4097),
    "reply-hidden-text": lambda wa: wa.create_whatsapp_saved_reply("T", _HIDDEN),
    "reply-hidden-title": lambda wa: wa.create_whatsapp_saved_reply(_HIDDEN, "text"),
    "reply-bad-shortcut": lambda wa: wa.create_whatsapp_saved_reply("T", "x", "/!!"),
    "reply-other-account": lambda wa: wa.create_whatsapp_saved_reply(
        "T", "x", account_id=STRANGER),
    "reply-account-path": lambda wa: wa.create_whatsapp_saved_reply(
        "T", "x", account_id="../chats"),
    "reply-too-big-for-card": lambda wa: wa.create_whatsapp_saved_reply("T", "b" * 4000),
    "delete-bad-id": lambda wa: wa.delete_whatsapp_saved_reply("1/../2"),
    "delete-unknown": lambda wa: wa.delete_whatsapp_saved_reply(STRANGER),
    "delete-other-number": lambda wa: wa.delete_whatsapp_saved_reply(REPLY, ACC2),
    "category-bad-id": lambda wa: wa.update_whatsapp_category("vip", notify_policy="never"),
    "category-unknown": lambda wa: wa.update_whatsapp_category(
        STRANGER, notify_policy="never"),
    "category-bad-value": lambda wa: wa.update_whatsapp_category(CAT, notify_policy="loud"),
    "category-no-value": lambda wa: wa.update_whatsapp_category(CAT),
    "category-no-change": lambda wa: wa.update_whatsapp_category(
        CAT, notify_policy="instant"),
    "seed-other-account": lambda wa: wa.seed_whatsapp_categories(STRANGER),
    "snooze-bad-id": lambda wa: wa.snooze_whatsapp_chat("c1", UNTIL),
    "snooze-unknown": lambda wa: wa.snooze_whatsapp_chat(STRANGER, UNTIL),
    "snooze-no-zone": lambda wa: wa.snooze_whatsapp_chat(CHAT, "2031-01-01T09:00:00"),
    "snooze-date-only": lambda wa: wa.snooze_whatsapp_chat(CHAT, "2031-01-01"),
    "snooze-words": lambda wa: wa.snooze_whatsapp_chat(CHAT, "tomorrow"),
    "snooze-past": lambda wa: wa.snooze_whatsapp_chat(CHAT, "2020-01-01T09:00:00Z"),
    "snooze-too-far": lambda wa: wa.snooze_whatsapp_chat(CHAT, _future(500).isoformat()),
    "unsnooze-bad-id": lambda wa: wa.unsnooze_whatsapp_chat("c1"),
    "unsnooze-not-snoozed": lambda wa: wa.unsnooze_whatsapp_chat(CHAT),
    "capture-bad-chat": lambda wa: wa.capture_whatsapp_task("c1", MSG),
    "capture-bad-message": lambda wa: wa.capture_whatsapp_task(CHAT, "m1"),
    "capture-hidden-title": lambda wa: wa.capture_whatsapp_task(CHAT, MSG, _HIDDEN),
    "capture-long-title": lambda wa: wa.capture_whatsapp_task(CHAT, MSG, "t" * 201),
}


@pytest.mark.parametrize("case", list(BAD))
async def test_a_bad_call_makes_no_card_and_no_write(gw, wa, case: str) -> None:
    out, cards = await _with_card(lambda: BAD[case](wa))
    assert cards == [], cards
    assert gw.writes() == []
    assert re.search(r"[Nn]othing was changed", out), out


@pytest.mark.parametrize("case", ["delete-bad-id", "category-bad-id", "snooze-bad-id",
                                  "unsnooze-bad-id", "capture-bad-chat",
                                  "capture-bad-message", "snooze-no-zone"])
async def test_a_bad_shape_makes_no_request_at_all(gw, wa, case: str) -> None:
    await BAD[case](wa)
    assert gw.calls == []


async def test_a_message_with_a_hidden_character_gets_no_card(gw, wa) -> None:
    gw.messages[CHAT][0]["body_text"] = _HIDDEN
    out, cards = await _with_card(lambda: wa.capture_whatsapp_task(CHAT, MSG))
    assert cards == [] and gw.writes() == []
    assert "cannot see" in out


async def test_a_message_of_another_chat_is_not_captured(gw, wa) -> None:
    with pytest.raises(RuntimeError, match="Message not found"):
        await _with_card(lambda: wa.capture_whatsapp_task(SNOOZED, MSG))
    assert gw.writes() == []


async def test_a_stored_reply_too_long_for_a_card_is_not_deleted(gw, wa) -> None:
    gw.replies[ACC][0]["body"] = "b" * 4096
    out, cards = await _with_card(lambda: wa.delete_whatsapp_saved_reply(REPLY))
    assert cards == [] and gw.writes() == []
    assert "does not fit whole on one card" in out


async def test_a_stored_name_cannot_forge_a_card_line(gw, wa) -> None:
    gw.replies[ACC][0]["title"] = "A\nShortcut: /fake\u200b"
    _out, cards = await _with_card(
        lambda: wa.delete_whatsapp_saved_reply(REPLY), _reject)
    assert "Title: A Shortcut: /fake\n" in cards[0]["context"]
    assert "\u200b" not in json.dumps(cards[0], ensure_ascii=False)


async def test_seed_with_each_default_there_shows_no_card(gw, wa) -> None:
    gw.categories[ACC] = [{"id": _id(60 + k), "name": n[0]}
                          for k, n in enumerate(_DEFAULT_NAMES)]
    out, cards = await _with_card(lambda: wa.seed_whatsapp_categories())
    assert cards == [] and gw.writes() == []
    assert "already" in out


async def test_a_chat_with_no_name_takes_the_contact_name(gw, wa) -> None:
    gw.messages[CHAT][0]["chat_name"] = ""
    _out, cards = await _with_card(
        lambda: wa.snooze_whatsapp_chat(CHAT, UNTIL), _reject)
    assert "Chat: Anil" in cards[0]["context"]


async def test_a_second_snooze_names_the_time_it_replaces(gw, wa) -> None:
    gw.messages[SNOOZED] = [{"id": _id(70), "chat_name": "Dealers North",
                             "chat_kind": "group"}]
    _out, cards = await _with_card(
        lambda: wa.snooze_whatsapp_chat(SNOOZED, UNTIL), _reject)
    assert "Chat: Dealers North (group)" in cards[0]["context"]
    assert "snoozed now until 2030-01-01" in cards[0]["context"]


@pytest.mark.parametrize(("name", "change"), [
    ("delete_whatsapp_saved_reply",
     lambda g: g.replies[ACC][0].update(body="New text")),
    ("delete_whatsapp_saved_reply", lambda g: g.replies[ACC].clear()),
    ("update_whatsapp_category",
     lambda g: g.categories[ACC][0].update(draft_policy="never")),
    ("update_whatsapp_category", lambda g: g.categories[ACC].clear()),
    ("seed_whatsapp_categories",
     lambda g: g.categories[ACC].append({"id": _id(80), "name": "Noise"})),
    ("seed_whatsapp_categories", lambda g: g.categories[ACC].clear()),
    ("capture_whatsapp_task",
     lambda g: g.messages[CHAT][0].update(body_text="Send the quote by Monday")),
    ("capture_whatsapp_task",
     lambda g: g.messages[CHAT][0].update(body_text="Send the quote by Friday\u200b")),
], ids=["reply-edited", "reply-gone", "category-edited", "category-gone",
        "seed-one-added", "seed-one-gone", "message-edited", "message-now-hidden"])
async def test_a_row_that_changed_while_the_card_waited_is_not_written(
    gw, wa, name: str, change,
) -> None:
    """Mutation: drop the second read -> red."""
    def answer(rid: str) -> dict[str, Any]:
        change(gw)
        return _approve(rid)

    out, cards = await _with_card(lambda: CASES[name][0](wa), answer)
    assert len(cards) == 1 and gw.writes() == []
    assert "changed while the card was open" in out


@pytest.mark.parametrize("name", [
    "delete_whatsapp_saved_reply", "update_whatsapp_category",
    "seed_whatsapp_categories", "capture_whatsapp_task",
])
async def test_a_second_read_that_fails_writes_nothing(gw, wa, name: str) -> None:
    def answer(rid: str) -> dict[str, Any]:
        gw.forced["GET"] = (500, {"detail": "boom"})
        return _approve(rid)

    out, cards = await _with_card(lambda: CASES[name][0](wa), answer)
    assert len(cards) == 1 and gw.writes() == []
    assert out.startswith("Nothing was changed.") and "could not be read again" in out


async def test_a_message_that_left_while_the_card_waited_is_not_captured(gw, wa) -> None:
    def answer(rid: str) -> dict[str, Any]:
        gw.messages[CHAT].clear()
        return _approve(rid)

    out, cards = await _with_card(lambda: CASES["capture_whatsapp_task"][0](wa), answer)
    assert len(cards) == 1 and gw.writes() == []
    assert out.startswith("Nothing was changed."), out


# ── 4. The errors of the route ───────────────────────────────────────────────


@pytest.mark.parametrize("name", WRITES)
@pytest.mark.parametrize("status", [403, 404, 409, 422])
async def test_a_refusal_of_the_route_says_nothing_changed(gw, wa, name, status) -> None:
    method = CASES[name][1][0]
    gw.forced[method] = (status, {"detail": "the route says no"})
    out, _cards = await _with_card(lambda: CASES[name][0](wa))
    assert out.startswith("Nothing was changed."), out
    assert f"({status}): the route says no" in out
    assert len(gw.writes()) == 1
    assert "SECRET-TOKEN" not in out


@pytest.mark.parametrize("name", WRITES)
@pytest.mark.parametrize("fault", ["500", "lost"])
async def test_a_fault_after_the_send_says_it_may_have_changed(gw, wa, name, fault) -> None:
    method = CASES[name][1][0]
    if fault == "500":
        gw.forced[method] = (500, {"detail": "boom"})
    else:
        gw.lost.add(method)
    out, _cards = await _with_card(lambda: CASES[name][0](wa))
    assert out.startswith("The change may or may not have happened."), out
    assert len(gw.writes()) == 1, "no second send"
    assert "SECRET-TOKEN" not in out


async def test_a_message_that_is_captured_already_makes_no_claim(gw, wa) -> None:
    gw.captured = False
    out, _cards = await _with_card(lambda: wa.capture_whatsapp_task(CHAT, MSG))
    assert out.startswith("No new task was made."), out


async def test_the_default_categories_that_exist_are_not_claimed(gw, wa) -> None:
    out, _cards = await _with_card(lambda: wa.seed_whatsapp_categories())
    assert out.startswith("Added 5 categories: New customer"), out
    assert "VIP" not in out


async def test_the_saved_reply_list_shows_the_id_that_a_delete_needs(gw, wa) -> None:
    assert f"(reply id={REPLY})" in await wa.list_whatsapp_saved_replies(ACC)


# ── 5. A held chat ───────────────────────────────────────────────────────────


def _switch(monkeypatch, orgs: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "held_card_writes_orgs", orgs)


def _built_agent(wa) -> Any:
    """The agent as production builds it, with the gate on its own tools."""
    agent = wa.build_agents()[0]
    ti._gate_own_maf_tools(agent.default_options["tools"])
    return agent


def _names(tools: list[Any]) -> set[str]:
    return {getattr(t, "name", "") for t in tools}


def test_the_held_table_names_each_card_tool_and_no_other() -> None:
    """A new card write with no row in ``HELD_OFF_OWN_WRITES`` stays in a held
    chat. Mutation: drop a name from the table -> red."""
    tree = ast.parse((AGENT_DIR / "agents.py").read_text(encoding="utf-8"))
    card: set[str] = set()
    for fn in tree.body:
        if not isinstance(fn, ast.AsyncFunctionDef) or fn.name.startswith("_"):
            continue
        calls = [c.func.id for c in ast.walk(fn)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
        if "_card" in calls or "_write" in calls:
            card.add(fn.name)
            assert calls.count("_card") == 1 and calls.count("_write") == 1, fn.name
            assert not {"_post", "_request"} & set(calls), fn.name
    assert card == set(WRITES)
    assert set(egress.HELD_OFF_OWN_WRITES[OWNER]) == set(WRITES)


def test_each_write_says_what_it_is(wa) -> None:
    tools = {fn.__name__: fn for fn in wa._TOOLS}
    for name in WRITES:
        risk = tools[name].__tool_risk__
        assert risk["read_only"] is False and risk["open_world"] is False, name
        assert risk["destructive"] is (name == "delete_whatsapp_saved_reply"), name
        assert egress.is_egress_tool(tools[name]) is False, name
        assert egress.is_held_card_write(tools[name]) is False, name


@pytest.mark.parametrize("orgs", ["*", ""], ids=["switch-on", "switch-off"])
def test_the_rule_holds_off_the_writes_only_with_the_switch(wa, monkeypatch, orgs) -> None:
    _switch(monkeypatch, orgs)
    for tool in _built_agent(wa).default_options["tools"]:
        held = bool(orgs) and tool.name in WRITES
        assert egress.is_held_off_write(tool, owner=OWNER) is held, tool.name
        assert egress.is_held_chat_off(tool, owner=OWNER) is held, tool.name
        assert egress.is_held_off_write(tool, owner="orchestrator") is False, tool.name


def test_a_held_delegated_run_drops_the_writes_and_keeps_the_rest(wa, monkeypatch) -> None:
    """The ``no_egress`` run, for example a delegation from the email assistant.
    Mutation: drop the table from ``is_held_off_write`` -> red."""
    _switch(monkeypatch, "*")
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: False)
    agents = [_built_agent(wa)]
    before = _names(agents[0].default_options["tools"])
    assert set(WRITES) <= before
    ti._apply_no_egress(agents, OWNER)
    assert _names(agents[0].default_options["tools"]) == before - set(WRITES)


@pytest.mark.parametrize(("marked", "off"), [(True, True), (False, False)])
def test_a_run_under_a_covered_run_holds_the_writes_only_when_marked(
    wa, monkeypatch, marked: bool, off: bool,
) -> None:
    """The documents name this exception to the hold of a ``no_egress`` run."""
    _switch(monkeypatch, "*")
    monkeypatch.setattr(ti, "_run_covered", lambda *_a, **_k: False)
    monkeypatch.setattr(egress, "covered_chain", lambda: True)
    monkeypatch.setattr(egress, "chat_marked_now", lambda *_a, **_k: marked)
    agents = [_built_agent(wa)]
    before = _names(agents[0].default_options["tools"])
    ti._apply_no_egress(agents, OWNER)
    after = _names(agents[0].default_options["tools"])
    assert after == before - (set(WRITES) if off else set())


@pytest.mark.parametrize(("marked", "off"), [(True, True), (False, False)])
async def test_a_marked_chat_refuses_each_write_at_the_call(
    wa, monkeypatch, marked: bool, off: bool,
) -> None:
    """The top-level run: ``ChatHoldProvider`` joins it, and while the chat is
    marked each write leaves the request and a call of one does not run."""
    _switch(monkeypatch, "*")
    monkeypatch.setattr(egress, "chat_marked_now", lambda *_a, **_k: marked)
    agents = [_built_agent(wa)]
    tools = {t.name: t for t in agents[0].default_options["tools"]}
    ti._apply_chat_hold(agents, OWNER)
    [hold] = [p for p in agents[0].context_providers
              if isinstance(p, egress.ChatHoldProvider)]

    class _Context:
        def __init__(self) -> None:
            self.items: list[Any] = []

        def extend_middleware(self, _source: str, items: list[Any]) -> None:
            self.items.extend(items)

    context = _Context()
    await hold.before_run(agent=None, session=None, context=context, state={})
    withhold, refuse = context.items

    class _Request:
        def __init__(self) -> None:
            self.options = {"tools": list(tools.values())}

    request = _Request()

    async def nothing() -> None:
        return None

    await withhold.process(request, nothing)
    kept = _names(request.options["tools"])
    assert kept == set(tools) - (set(WRITES) if off else set())

    for name in WRITES:
        ran: list[bool] = []

        class _Call:
            function = tools[name]
            result: Any = None

        async def call_next(seen: list[bool] = ran) -> None:
            seen.append(True)

        call = _Call()
        await refuse.process(call, call_next)
        assert bool(ran) is not off, name
        if off:
            assert f"{name} is off now" in call.result


# ── 6. The registration, the parity rows and the copies of the gateway ───────


def test_the_agent_holds_the_seven_tools(wa) -> None:
    cfg = json.loads((AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    names = {fn.__name__ for fn in wa._TOOLS}
    assert set(WRITES) <= names and set(WRITES) <= set(cfg["own_tool_scope"])
    guide = (AGENT_DIR / "instructions.md").read_text(encoding="utf-8")
    for name in WRITES:
        assert f"`{name}`" in guide, name
    assert not any("send" in n or "broadcast" in n for n in names)


_ROWS = {
    "POST /api/whatsapp/saved-replies": "create_whatsapp_saved_reply",
    "DELETE /api/whatsapp/saved-replies/{}": "delete_whatsapp_saved_reply",
    "PATCH /api/whatsapp/categories/{}": "update_whatsapp_category",
    "POST /api/whatsapp/accounts/{}/categories/bootstrap": "seed_whatsapp_categories",
    "POST /api/whatsapp/chats/{}/snooze": "snooze_whatsapp_chat",
    "POST /api/whatsapp/chats/{}/unsnooze": "unsnooze_whatsapp_chat",
    "POST /api/whatsapp/capture-task": "capture_whatsapp_task",
}


def test_the_seven_rows_are_covered_and_the_code_row_stays_debt() -> None:
    """The link code binds a phone to a member, so no tool sends it."""
    for action, tool in _ROWS.items():
        assert parity.COVERED[action] == tool and action not in parity.DEBT, action
    assert "POST /api/me/whatsapp-link/code" in parity.DEBT
    left = [a for a in parity.DEBT if parity.app_of(a.split(" ", 1)[1]) == "whatsapp"]
    assert parity.DEBT_CEILING["whatsapp"] == len(left) == 6
    source = (AGENT_DIR / "agents.py").read_text(encoding="utf-8")
    assert "/me/whatsapp-link/code" not in source


def test_the_limits_and_the_shortcut_match_the_gateway(wa) -> None:
    from gateway.routes.whatsapp.transport import saved_replies as route

    assert (wa._TITLE_MAX, wa._BODY_MAX, wa._SHORTCUT_MAX) == (
        route._MAX_TITLE, route._MAX_BODY, route._MAX_SHORTCUT)
    for raw in ("/Price List", "//a", "GST_no", "!!", " /x-y ", "a" * 50, "é1"):
        assert wa._saved_shortcut(raw) == route.normalize_shortcut(raw), raw


def test_the_policies_and_the_defaults_match_the_gateway(wa) -> None:
    from gateway.routes.whatsapp.automation import categories as route

    assert set(wa._POLICIES["notify_policy"][1]) == route._NOTIFY
    assert set(wa._POLICIES["auto_reply_policy"][1]) == route._AUTO_REPLY
    assert set(wa._POLICIES["draft_policy"][1]) == route._DRAFT
    want = tuple((c["name"], c["notify_policy"], c["auto_reply_policy"],
                  c["draft_policy"], c["escalate_after_mins"])
                 for c in route.default_categories())
    assert want == wa._DEFAULT_CATEGORIES


def test_only_the_rule_preview_reads_the_policies() -> None:
    """The cards say that Metorite sends no automatic reply from a policy. A new
    reader makes that claim suspect. Mutation: read ``auto_reply_policy`` in a
    send path -> red. Then change ``_POLICY_NOTE``."""
    readers: set[str] = set()
    callers: set[str] = set()
    for root in ("apps", "packages"):
        for path in (REPO / root).rglob("*.py"):
            if {"node_modules", ".venv", "__pycache__"} & set(path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = path.relative_to(REPO).as_posix()
            if re.search(r"notify_policy|auto_reply_policy|draft_policy", text):
                readers.add(rel)
            if re.search(r"\bdecide_action\(", text):
                callers.add(rel)
    base = "apps/services/gateway/gateway/routes/whatsapp/automation"
    assert readers == {f"{base}/categories.py", f"{base}/rules.py",
                       "apps/agents/agent-whatsapp-assistant/agents.py"}
    assert callers == {f"{base}/rules.py"}


def test_the_wake_time_rule_matches_the_gateway(wa) -> None:
    from gateway.routes.whatsapp.transport import chats, snooze

    assert wa._SNOOZE_MAX_DAYS == snooze._MAX_SNOOZE_DAYS
    limit = chats.list_chats.__defaults__
    assert limit is not None
    assert any(getattr(m, "le", None) == wa._CHATS_MAX
               for d in limit for m in getattr(d, "metadata", ()))
    now = datetime.now(UTC)
    for days in (1, 399, 400):
        when = (now + timedelta(days=days, hours=1)).isoformat()
        assert wa._wake_time(when)[0] is not None
        snooze.parse_snooze_until(when, now)
    far = (now + timedelta(days=402)).isoformat()
    assert wa._wake_time(far)[0] is None
    with pytest.raises(ValueError, match="too far"):
        snooze.parse_snooze_until(far, now)


@pytest.mark.parametrize(("body", "sender", "chat"), [
    ("Send  the\nquote", "Ravi", "Shop"), ("", "", "Shop"), ("x" * 300, "", ""),
    ("", "", ""), ("short", "  ", "Chat"),
])
def test_the_task_title_matches_the_gateway(wa, body, sender, chat) -> None:
    from gateway.routes.whatsapp.transport.capture import derive_title

    assert wa._task_title(body, sender, chat) == derive_title(body, sender, chat)


def test_each_write_matches_a_real_route_and_its_body_model() -> None:
    """The fake gateway agrees with any path and body. This holds each write of
    :data:`CASES` to a route of the gateway and to the model of its body.
    Mutation: send a body key that the route does not know -> red."""
    from gateway.routes.whatsapp import router

    for name, (_call, (method, path, body), _title, _words) in CASES.items():
        [route] = [r for r in router.routes
                   if method in getattr(r, "methods", ()) and r.path_regex.match(path)]
        if not body:
            assert route.body_field is None, name
            continue
        model = route.body_field.field_info.annotation
        assert set(body) <= set(model.model_fields), name
        model.model_validate(body)


# ── 7. The cards state the real effect ───────────────────────────────────────


async def test_a_name_with_new_lines_cannot_forge_the_task_card(gw, wa) -> None:
    """The title that goes is the one line that the card shows. Mutation: drop
    ``one_line`` from the sender of ``capture_whatsapp_task`` -> red."""
    gw.messages[CHAT][0].update(
        sender_name="Ravi\nTask title: fake\u2028x", chat_name="Shop\nFrom: boss")
    out, cards = await _with_card(lambda: wa.capture_whatsapp_task(CHAT, MSG))
    [(_method, _path, body)] = gw.writes()
    want = "WhatsApp · Ravi Task title: fake x: Send the quote by Friday"
    assert body["title"] == want
    lines = cards[0]["context"].splitlines()
    assert [ln for ln in lines if ln.startswith("Task title:")] == [f"Task title: {want}"]
    assert [ln for ln in lines if ln.startswith("From:")] == [
        "From: Ravi Task title: fake x"]
    assert lines[0] == "Chat: Shop From: boss"
    assert out.startswith("Added the task")


@pytest.mark.parametrize("kind", ["group", "dm"])
async def test_a_snooze_card_does_not_warn_about_needs_reply(gw, wa, kind: str) -> None:
    """Task 4011 is fixed: ``recompute_chat_status`` reads the account phone, so a
    snooze keeps a group mention. A card that still warned would be false. The
    fix has its own R8 suite, ``test_whatsapp_snooze_mention_r8.py``."""
    gw.messages[CHAT][0]["chat_kind"] = kind
    _out, cards = await _with_card(
        lambda: wa.snooze_whatsapp_chat(CHAT, UNTIL), _reject)
    assert "Needs reply" not in cards[0]["context"]


@pytest.mark.parametrize(("number", "provider", "promised"), [
    (ACC, "cloud_api", True), (ACC, "cloud", True), (ACC2, "whatsmeow", False),
    (ACC2, "", False),
])
async def test_the_wake_promise_shows_for_a_cloud_number_only(
    gw, wa, number: str, provider: str, promised: bool,
) -> None:
    """The bridge routes bind no organization, so no sweep clears a snooze of a
    bridge number. Mutation: show the sentence for each number -> red."""
    gw.chat_account[CHAT] = number
    next(a for a in gw.accounts if a["id"] == number)["provider"] = provider
    _out, cards = await _with_card(
        lambda: wa.snooze_whatsapp_chat(CHAT, UNTIL), _reject)
    context = cards[0]["context"]
    assert ("wakes the chat sooner" in context) is promised
    assert context.endswith("Nothing is sent to them.")


async def test_a_full_snoozed_list_without_the_chat_shows_no_wake_card(gw, wa) -> None:
    """Mutation: wake a chat that the full list does not hold -> red."""
    gw.snoozed = [{"id": _id(1000 + k), "name": f"c{k}"} for k in range(200)]
    out, cards = await _with_card(lambda: wa.unsnooze_whatsapp_chat(CHAT))
    assert cards == [] and gw.writes() == []
    assert "cannot confirm" in out and "Nothing was changed." in out
    held = _id(1007)
    out, cards = await _with_card(lambda: wa.unsnooze_whatsapp_chat(held))
    assert len(cards) == 1 and gw.writes() == [
        ("POST", f"/whatsapp/chats/{held}/unsnooze", {})]


def test_the_workflow_line_is_still_true_of_the_capture() -> None:
    """The capture path sends ``pm.task.created``, and a workflow can start on it."""
    routes = REPO / "apps/services/gateway/gateway/routes"
    assert 'emit("pm.task.created"' in (routes / "projects/personal.py").read_text(
        encoding="utf-8")
    assert '"event_type": "pm.task.created"' in (routes / "workflows/catalog.py").read_text(
        encoding="utf-8")


def test_a_whatsapp_read_marks_no_chat() -> None:
    """The documents say that the card is the only guard for injected WhatsApp
    text (task 4010). When a read starts to mark the chat, this goes red. Then
    change those words."""
    assert OWNER not in egress.UNTRUSTED_INPUT_AGENTS
    for name in ("agents.py", "narrow_source.py"):
        assert "mark_untrusted" not in (AGENT_DIR / name).read_text(encoding="utf-8"), name
