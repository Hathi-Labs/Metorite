"""WS-17 EM-T8e-2 — the chat tools bind to one mailbox (agent).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.5, under
"EM-T8e-2". The order is §11.3. Decisions D-EM-19, D-EM-20, D-EM-22 and
D-EM-23 (§11.2). Defect MB-15 (§11.1).

The gateway is a fake, so this file needs no database. The R8 half of the
``sent-from`` read is ``tests/unit/test_email_from_row.py``.

R7 fences named here:

* ``email-chat-reply-refuses-other-mailbox``: a reply that names another
  mailbox sends nothing, shows no card, and names the mailbox of the mail as
  "label · address". A reply that names no mailbox, or the right one, goes
  out from the mailbox of the mail.
* ``email-chat-new-mail-binding``: new mail with no ``account_id`` uses the
  only mailbox. With two, it uses the mailbox that ``sent-from`` names for
  the first recipient, in lower case. An empty answer, an answer that names
  no mailbox of the member, or a failed read gives the question "Send from
  which mailbox?" and sends nothing. ``instructions.md`` rule 3 names the
  same step.
* ``email-chat-rule-asks``: each tool of item 3 asks "Which mailbox?" with
  two mailboxes and no ``account_id``, and writes nothing. With one mailbox,
  each uses it.
* ``email-chat-bulk-no-account``: ``manage_inbox`` sends no ``account_id``,
  and takes none.
* ``email-chat-thread-mailbox``: ``read_thread`` reads in the mailbox of the
  mail and takes no ``account_id``. A thread id that two mailboxes hold is
  not merged (D-EM-22). This fence is not in the spec list. It fences the
  ``read_thread`` rule of item 1 (R7).
* ``email-chat-label-address``: each result tag, each From line and
  ``list_accounts`` carry "label · address" from ``display_label``, never
  the raw label "Outlook" (MB-15).
* ``email-chat-ui-shapes``: the row is ``id=<id> [<tag>] | …`` and the
  first line of a draft is ``Draft from <from> (mailbox <id>)``, as
  ``EmailToolCards.tsx`` parses them. A question never holds ``id=``.

Run::

    uv run pytest tests/unit/test_email_chat_binding.py -v -rs
"""
from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from agent_framework import FunctionTool

_AGENT = (
    Path(__file__).resolve().parents[2]
    / "apps" / "agents" / "agent-email-assistant" / "agents.py"
)


def _load_agents():
    spec = importlib.util.spec_from_file_location("ea_chat_binding", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_agents()

# Ids shaped like the real ones, so a stray ``id=<uuid>`` in a question would
# match ``RULE_ID_RE`` of the rule card.
BOX_A = "0a0a0a0a-0000-4000-8000-00000000000a"
BOX_B = "0b0b0b0b-0000-4000-8000-00000000000b"

# Both mailboxes carry the raw label "Outlook", the way a connect writes it.
# Only ``display_label`` tells them apart (MB-15, §11.4).
TWO = [
    {"id": BOX_A, "email_address": "dana@fracktal.in", "label": "Outlook",
     "display_label": "Fracktal", "unread_count": 2},
    {"id": BOX_B, "email_address": "dana@outlook.com", "label": "Outlook",
     "display_label": "Personal", "unread_count": 1},
]
A_TEXT = "Fracktal · dana@fracktal.in"
B_TEXT = "Personal · dana@outlook.com"

# Two mailboxes hold the thread id "t1" (edge case 13). "t3" is in B only.
THREAD = [
    {"id": "m1", "account_id": BOX_A, "thread_id": "t1", "subject": "Quote",
     "from_address": {"name": "Ravi", "email": "ravi@contoso.test"},
     "received_at": "2026-10-01", "body_text": "First"},
    {"id": "m2", "account_id": BOX_A, "thread_id": "t1", "subject": "Quote",
     "from_address": {"name": "Dana", "email": "dana@fracktal.in"},
     "received_at": "2026-10-02", "body_text": "Second", "folder": "sent"},
    {"id": "m9", "account_id": BOX_B, "thread_id": "t1", "subject": "Other",
     "from_address": {"name": "Kim", "email": "kim@contoso.test"},
     "received_at": "2026-10-02", "body_text": "Not this one"},
    {"id": "m7", "account_id": BOX_B, "thread_id": "t3", "subject": "Lunch",
     "from_address": {"name": "Kim", "email": "kim@contoso.test"},
     "received_at": "2026-10-02", "body_text": "Lunch?"},
]


class Gateway:
    """A fake gateway that records each call and each card."""

    def __init__(self) -> None:
        self.accounts: list[dict[str, Any]] = list(TWO)
        self.sent_from: Any = {}
        self.fail: set[str] = set()
        self.answer = True
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.writes: list[tuple[str, str, Any]] = []
        self.cards: list[dict[str, Any]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        self.gets.append((path, params))
        if path in self.fail:
            raise RuntimeError(f"Email GET {path} failed (503)")
        if path == "/email/accounts":
            return self.accounts
        if path == "/email/contacts/sent-from":
            return self.sent_from
        if path.startswith("/email/messages/"):
            mid = path.rsplit("/", 1)[1]
            return next((dict(m) for m in THREAD if m["id"] == mid), {})
        if path == "/email/messages":
            return self._list(params)
        if path == "/email/assistant/settings":
            return {"account_id": params.get("account_id"), "auto_run": False}
        if path == "/email/knowledge":
            return {"entries": [{"id": "k1", "title": "Old", "content": "c"}]}
        return {}

    @staticmethod
    def _list(params: dict[str, Any]) -> dict[str, Any]:
        rows = THREAD
        if params.get("thread_id"):
            rows = [m for m in rows if m["thread_id"] == params["thread_id"]]
        else:
            rows = [m for m in rows if m["id"] in ("m1", "m7")]
        if params.get("account_id"):
            rows = [m for m in rows if m["account_id"] == params["account_id"]]
        return {"emails": rows, "total": len(rows)}

    async def write(self, verb: str, path: str, body: Any = None) -> Any:
        self.writes.append((verb, path, body))
        return {"id": "new-1", "draft": "Hi Ravi", "created": [],
                "installed": ["To Reply"], "writing_style": "Short.",
                "count": 0, "affected": 2}

    async def confirm(self, **kw: Any) -> bool:
        self.cards.append(kw)
        return self.answer


@pytest.fixture()
def gw(monkeypatch: pytest.MonkeyPatch) -> Gateway:
    g = Gateway()

    async def _post(path: str, body: Any) -> Any:
        return await g.write("POST", path, body)

    async def _patch(path: str, body: Any) -> Any:
        return await g.write("PATCH", path, body)

    async def _delete(path: str) -> Any:
        return await g.write("DELETE", path)

    async def _put_settings(body: Any) -> Any:
        return await g.write("PUT", "/email/assistant/settings", body)

    monkeypatch.setattr(agents, "_get", g.get)
    monkeypatch.setattr(agents, "_post", _post)
    monkeypatch.setattr(agents, "_patch", _patch)
    monkeypatch.setattr(agents, "_delete", _delete)
    monkeypatch.setattr(agents, "_patch_settings", _put_settings)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", g.confirm)
    return g


def _sends(g: Gateway) -> list[dict[str, Any]]:
    return [body for verb, path, body in g.writes if path == "/email/send"]


async def _as_the_model(fn: Any, **arguments: Any) -> str:
    """Call a tool the way the MAF agent calls it, with the JSON arguments of
    the model. MAF drops an argument that the tool does not declare."""
    tool = FunctionTool(func=fn, name=fn.__name__)
    [content] = await tool.invoke(arguments=arguments)
    return str(content.text)


# ── email-chat-reply-refuses-other-mailbox ───────────────────────────────────

async def test_a_reply_that_names_another_mailbox_sends_nothing(gw: Gateway) -> None:
    out = await agents.send_email(BOX_B, body="Thanks!", reply_to_email_id="m1")
    assert gw.writes == [] and gw.cards == []
    assert out.startswith("Not sent.")
    # The right mailbox, as "label · address", and the id to call again with.
    assert f"{A_TEXT} (account_id {BOX_A})" in out
    assert B_TEXT in out


async def test_a_reply_that_names_no_mailbox_sends_from_the_mail(gw: Gateway) -> None:
    out = await agents.send_email(body="Thanks!", reply_to_email_id="m1")
    [sent] = _sends(gw)
    assert sent["account_id"] == BOX_A
    assert sent["reply_to_message_id"] == "m1"
    assert sent["to"] == ["ravi@contoso.test"]
    [card] = gw.cards
    assert card["detail"].startswith(f"From {A_TEXT} · To ravi@contoso.test")
    assert f"from {A_TEXT}" in out
    # A reply never asks sent-from: the mail decides.
    assert all(p != "/email/contacts/sent-from" for p, _ in gw.gets)


async def test_a_reply_that_names_its_own_mailbox_sends(gw: Gateway) -> None:
    await agents.send_email(BOX_A, body="Thanks!", reply_to_email_id="m1")
    [sent] = _sends(gw)
    assert sent["account_id"] == BOX_A


async def test_a_declined_reply_card_sends_nothing(gw: Gateway) -> None:
    gw.answer = False
    out = await agents.send_email(body="Thanks!", reply_to_email_id="m1")
    assert "Send cancelled" in out and _sends(gw) == []


# ── email-chat-new-mail-binding ──────────────────────────────────────────────

async def test_new_mail_with_one_mailbox_sends_from_it(gw: Gateway) -> None:
    gw.accounts = TWO[1:]
    await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    [sent] = _sends(gw)
    assert sent["account_id"] == BOX_B
    assert all(p != "/email/contacts/sent-from" for p, _ in gw.gets)


async def test_new_mail_sends_from_the_mailbox_that_last_wrote(gw: Gateway) -> None:
    gw.sent_from = {"kim@contoso.test": BOX_B}
    out = await agents.send_email(
        body="Hi", to=["Kim <KIM@Contoso.test>", "lee@contoso.test"], subject="S")
    # The first recipient, as a bare address, in lower case.
    [params] = [p for path, p in gw.gets if path == "/email/contacts/sent-from"]
    assert params == {"emails": "kim@contoso.test"}
    [sent] = _sends(gw)
    assert sent["account_id"] == BOX_B
    [card] = gw.cards
    assert card["detail"].startswith(f"From {B_TEXT} · To ")
    assert f"from {B_TEXT}" in out


@pytest.mark.parametrize("answer", [
    {},                                         # no mailbox wrote to Kim
    {"kim@contoso.test": "box-of-someone-else"},  # not a mailbox of the member
    ["not", "a", "map"],                        # a shape the route never sends
])
async def test_new_mail_with_no_match_asks_and_sends_nothing(
    gw: Gateway, answer: Any,
) -> None:
    gw.sent_from = answer
    out = await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    assert gw.writes == [] and gw.cards == []
    assert out.startswith("Send from which mailbox?")
    assert f"• {A_TEXT} (account_id {BOX_A})" in out
    assert f"• {B_TEXT} (account_id {BOX_B})" in out


async def test_a_failed_sent_from_read_asks_rather_than_guesses(gw: Gateway) -> None:
    gw.fail.add("/email/contacts/sent-from")
    out = await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    assert out.startswith("Send from which mailbox?")
    assert gw.writes == [] and gw.cards == []


async def test_a_failed_mailbox_read_sends_nothing(gw: Gateway) -> None:
    gw.fail.add("/email/accounts")
    out = await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    assert out.startswith("Not sent.")
    assert gw.writes == [] and gw.cards == []


async def test_a_named_mailbox_needs_no_sent_from(gw: Gateway) -> None:
    gw.sent_from = {"kim@contoso.test": BOX_B}
    await agents.send_email(BOX_A, body="Hi", to=["kim@contoso.test"], subject="S")
    [sent] = _sends(gw)
    assert sent["account_id"] == BOX_A
    assert all(p != "/email/contacts/sent-from" for p, _ in gw.gets)


def test_the_instructions_take_the_sent_from_step() -> None:
    text = agents.INSTRUCTIONS
    assert "last wrote to the first recipient" in text
    assert "Send from which mailbox?" in text


# ── email-chat-rule-asks ─────────────────────────────────────────────────────

# Each tool of item 3, with the arguments it needs other than account_id.
RULE_TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("create_rule", {"name": "Bank"}),
    ("create_rules_from_prompt", {"prompt": "Label my bank mail Finance"}),
    ("install_default_rules", {"reset": True}),
    ("update_assistant_settings", {"signature": "Dana"}),
    ("save_knowledge", {"title": "Prices", "content": "Ten rupees"}),
    ("generate_writing_style", {}),
    ("learn_rule_pattern", {"rule_id": "r1", "sender": "bank.test"}),
    ("run_rules", {"scope": "past", "days": 3}),
]


def test_item_3_names_every_tool_of_the_spec() -> None:
    assert [name for name, _ in RULE_TOOLS] == [
        "create_rule", "create_rules_from_prompt", "install_default_rules",
        "update_assistant_settings", "save_knowledge", "generate_writing_style",
        "learn_rule_pattern", "run_rules",
    ]


@pytest.mark.parametrize(("tool", "kwargs"), RULE_TOOLS, ids=[t for t, _ in RULE_TOOLS])
async def test_two_mailboxes_and_no_account_asks_which(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    out = await getattr(agents, tool)(**kwargs)
    assert out.startswith("Which mailbox?")
    assert f"• {A_TEXT} (account_id {BOX_A})" in out
    assert f"• {B_TEXT} (account_id {BOX_B})" in out
    assert f"call {tool} again" in out
    # Nothing written and no card, not even the reset card.
    assert gw.writes == [] and gw.cards == []


@pytest.mark.parametrize(("tool", "kwargs"), RULE_TOOLS, ids=[t for t, _ in RULE_TOOLS])
async def test_one_mailbox_and_no_account_uses_it(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    gw.accounts = TWO[1:]
    out = await getattr(agents, tool)(**kwargs)
    assert not out.startswith("Which mailbox?")
    assert gw.writes, f"{tool} wrote nothing"
    for _verb, path, body in gw.writes:
        assert BOX_B in f"{path} {body}", (tool, path, body)
        assert BOX_A not in f"{path} {body}", (tool, path, body)


@pytest.mark.parametrize(("tool", "kwargs"), RULE_TOOLS, ids=[t for t, _ in RULE_TOOLS])
async def test_a_named_mailbox_is_used_with_no_question(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    out = await getattr(agents, tool)(BOX_A, **kwargs)
    assert not out.startswith("Which mailbox?")
    assert all(p != "/email/accounts" for p, _ in gw.gets)
    assert gw.writes and all(BOX_A in f"{p} {b}" for _v, p, b in gw.writes)


async def test_a_failed_mailbox_read_changes_nothing(gw: Gateway) -> None:
    gw.fail.add("/email/accounts")
    out = await agents.create_rule(name="Bank")
    assert out.startswith("Nothing changed.")
    assert gw.writes == []


@pytest.mark.parametrize(("tool", "kwargs"), RULE_TOOLS, ids=[t for t, _ in RULE_TOOLS])
def test_account_id_is_optional_and_the_rest_stays_required(
    tool: str, kwargs: dict[str, Any],
) -> None:
    params = inspect.signature(getattr(agents, tool)).parameters
    assert params["account_id"].default is None
    for name in kwargs:
        if params[name].default is inspect.Parameter.empty:
            # A required argument after an optional one must be keyword-only,
            # or Python cannot express it and the schema loses "required".
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY


# ── email-chat-bulk-no-account ───────────────────────────────────────────────

async def test_the_bulk_act_sends_no_account_id(gw: Gateway) -> None:
    out = await agents.manage_inbox("archive", ["m1", "m7"])
    assert gw.writes == [("POST", "/email/messages/bulk",
                          {"action": "archive", "message_ids": ["m1", "m7"]})]
    assert "affected 2" in out


async def test_a_confirmed_trash_sends_no_account_id(gw: Gateway) -> None:
    await agents.manage_inbox("trash", ["m1"])
    [(_verb, _path, body)] = gw.writes
    assert "account_id" not in body


def test_manage_inbox_takes_no_account_id() -> None:
    assert "account_id" not in inspect.signature(agents.manage_inbox).parameters


async def test_an_account_id_from_the_model_never_reaches_the_bulk_body(
    gw: Gateway,
) -> None:
    # A wrong id once changed 0 rows and reported no error (§11.7.5).
    out = await _as_the_model(agents.manage_inbox, action="archive",
                              message_ids=["m1"], account_id=BOX_B)
    assert gw.writes == [("POST", "/email/messages/bulk",
                          {"action": "archive", "message_ids": ["m1"]})]
    assert "affected 2" in out


# ── email-chat-thread-mailbox ────────────────────────────────────────────────

async def test_a_thread_is_read_in_the_mailbox_of_the_mail(gw: Gateway) -> None:
    out = await agents.read_thread(email_id="m1")
    [params] = [p for path, p in gw.gets if path == "/email/messages"]
    assert params["thread_id"] == "t1" and params["account_id"] == BOX_A
    assert out.startswith("Thread: Quote — 2 message(s)")
    assert "Not this one" not in out


async def test_the_mail_wins_over_a_thread_id_from_the_model(gw: Gateway) -> None:
    await agents.read_thread(email_id="m1", thread_id="t3")
    [params] = [p for path, p in gw.gets if path == "/email/messages"]
    assert params == {"thread_id": "t1", "page_size": "50", "account_id": BOX_A}


async def test_a_thread_id_in_two_mailboxes_is_not_merged(gw: Gateway) -> None:
    out = await agents.read_thread(thread_id="t1")
    assert "in 2 mailboxes" in out and A_TEXT in out and B_TEXT in out
    assert "email_id" in out
    assert "First" not in out and "Not this one" not in out


async def test_a_thread_id_in_one_mailbox_is_read(gw: Gateway) -> None:
    out = await agents.read_thread(thread_id="t3")
    assert out.startswith("Thread: Lunch — 1 message(s)")


def test_read_thread_takes_no_account_id() -> None:
    assert "account_id" not in inspect.signature(agents.read_thread).parameters


async def test_an_account_id_from_the_model_never_wins_a_thread_read(
    gw: Gateway,
) -> None:
    out = await _as_the_model(agents.read_thread, email_id="m1", account_id=BOX_B)
    [params] = [p for path, p in gw.gets if path == "/email/messages"]
    assert params["account_id"] == BOX_A
    assert out.startswith("Thread: Quote — 2 message(s)")


# ── email-chat-label-address ─────────────────────────────────────────────────

@pytest.mark.parametrize("tool", ["search_emails", "find_urgent"])
async def test_each_result_tag_is_label_and_address(gw: Gateway, tool: str) -> None:
    fn = getattr(agents, tool)
    out = await (fn("quote") if tool == "search_emails" else fn())
    assert f"id=m1 [{A_TEXT}] |" in out
    assert f"id=m7 [{B_TEXT}] |" in out
    assert "[Outlook]" not in out


async def test_list_accounts_names_label_and_address(gw: Gateway) -> None:
    out = await agents.list_accounts()
    assert f"• {A_TEXT} — id={BOX_A}, 2 unread" in out
    assert f"• {B_TEXT} — id={BOX_B}, 1 unread" in out
    assert "Outlook (" not in out


async def test_each_from_line_is_label_and_address(gw: Gateway) -> None:
    await agents.send_email(BOX_B, body="Hi", to=["kim@contoso.test"], subject="S")
    await agents.send_draft(BOX_A, "d1")
    new_card, draft_card = gw.cards
    assert new_card["detail"].startswith(f"From {B_TEXT} · To ")
    assert draft_card["detail"].startswith(f"From {A_TEXT} · ")
    out = await agents.draft_reply("m1", BOX_B)
    assert out.startswith(f"Draft from {A_TEXT} (mailbox {BOX_A})")


def test_a_mailbox_with_no_label_of_its_own_is_its_address() -> None:
    same = {"id": "x", "email_address": "dana@fracktal.in",
            "display_label": "dana@fracktal.in", "label": "Outlook"}
    assert agents._mailbox_text(same) == "dana@fracktal.in"
    # An answer from before EM-T8b has no display_label: the raw label stays.
    old = {"id": "x", "email_address": "dana@fracktal.in", "label": "Fracktal"}
    assert agents._mailbox_text(old) == A_TEXT


# ── email-chat-ui-shapes ─────────────────────────────────────────────────────

# The regexes of ``EmailToolCards.tsx``: ``parseEmailRows``, ``draftReplyHead``
# and ``RULE_ID_RE``.
ROW_ID = re.compile(r"\bid=([^\s|\]]+)")
ROW_TAG = re.compile(r"^\s*\[[^\]]*\]")
DRAFT_HEAD = re.compile(r"^Draft from (.+?) \(mailbox ([^)\s]+)\)")
RULE_ID = re.compile(r"id=([0-9a-fA-F-]{8,})")


async def test_the_row_shape_parses_as_the_list_card_reads_it(gw: Gateway) -> None:
    out = await agents.search_emails("quote")
    rows = [line for line in out.splitlines() if ROW_ID.search(line)]
    parsed = []
    for line in rows:
        m = ROW_ID.search(line)
        assert m is not None
        rest = ROW_TAG.sub("", line[m.end():])
        last = [s.strip() for s in rest.split("|") if s.strip()][-1]
        parsed.append((m.group(1), last.split(":", 1)[0].strip()))
    assert parsed == [("m1", "Ravi"), ("m7", "Kim")]


async def test_the_first_draft_line_parses_as_the_draft_card_reads_it(gw: Gateway) -> None:
    out = await agents.draft_reply("m1", BOX_B)
    m = DRAFT_HEAD.match(out.split("\n", 1)[0])
    assert m is not None
    assert m.groups() == (A_TEXT, BOX_A)


async def test_a_question_never_holds_an_id_a_card_could_read(gw: Gateway) -> None:
    rule_q = await agents.create_rule(name="Bank")
    send_q = await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    thread_q = await agents.read_thread(thread_id="t1")
    for out in (rule_q, send_q, thread_q):
        assert RULE_ID.search(out) is None, out
        assert ROW_ID.search(out) is None, out
