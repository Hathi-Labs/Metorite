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
* ``email-chat-no-action-lead`` (review round 1): each answer of a send, bulk
  or item 3 tool that did not act starts with a lead that ``noActionOf`` in
  ``EmailToolCards.tsx`` knows, so no card says "Email sent" over a refusal.
  The test reads every ``return`` of those tools, not a fixed list.
* ``email-chat-no-mailbox`` (review round 1): with no mailbox connected, a
  send and each tool of item 3 change nothing and say so.
* ``email-chat-binding-skips-separate`` (EM-T8g-1, §11.7.7 item 5): a tool
  that names no mailbox binds with no question only when the member has
  exactly one mailbox in total. The question lists the mailboxes in All
  inboxes when two or more are there, else each mailbox. ``sent-from`` binds
  only a mailbox in All inboxes, and only when two or more are there. Review
  round 1 changed this fence: one pooled mailbox and a separate one now ask.
* ``email-chat-write-names-mailbox`` (EM-T8g-1 review round 1): each answer
  of an item 3 tool, and the reset card, name the mailbox.
* ``email-chat-list-accounts-separate`` (EM-T8g-1 review round 1):
  ``list_accounts`` marks a separate mailbox and leaves it out of the total.

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
# Only ``display_label`` tells them apart (MB-15, §11.4). Both are in All
# inboxes, as each row of migration 229 is until the member changes it.
TWO = [
    {"id": BOX_A, "email_address": "dana@fracktal.in", "label": "Outlook",
     "display_label": "Fracktal", "unread_count": 2, "in_all_inboxes": True},
    {"id": BOX_B, "email_address": "dana@outlook.com", "label": "Outlook",
     "display_label": "Personal", "unread_count": 1, "in_all_inboxes": True},
]
A_TEXT = "Fracktal · dana@fracktal.in"
B_TEXT = "Personal · dana@outlook.com"

# EM-T8g-1: a third mailbox that the member keeps separate (D-EM-28).
BOX_C = "0c0c0c0c-0000-4000-8000-00000000000c"
NDA = {"id": BOX_C, "email_address": "dana@client-nda.test", "label": "Outlook",
       "display_label": "Client NDA", "unread_count": 4, "in_all_inboxes": False}
C_TEXT = "Client NDA · dana@client-nda.test"

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
    # EM-T8g-1 review round 1: the answer names the mailbox, so the tool may
    # read the list for its name. The list never chooses the mailbox.
    assert gw.writes and all(BOX_A in f"{p} {b}" for _v, p, b in gw.writes)
    assert A_TEXT in out


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


# ── email-chat-no-action-lead (review round 1) ───────────────────────────────

import ast as _ast  # noqa: E402

NO_ACTION_LEADS = (
    "Not sent.", "Send from which mailbox?", "Which mailbox?", "Nothing changed.",
    "Send cancelled", "Cancelled",
)
# Each lead as the TypeScript regexes of ``noActionOf`` spell it.
TS_LEADS = (
    r"Not sent\.", r"Send from which mailbox\?", r"Which mailbox\?",
    r"Nothing changed\.", "Send cancelled", "Cancelled",
)
# The leads of an answer that DID act. "{" is an f-string that starts with a
# value, such as f"{lead} {to} from {sender}".
ACTING = {
    "send_email": ("{",),
    "send_draft": ("Draft sent.",),
    "manage_inbox": ("{", "Moved ", "Updated labels "),
}
HELPERS = ("_new_mail_mailbox", "_one_mailbox", "_refuse_reply_mailbox")
_CARDS = (
    Path(__file__).resolve().parents[2] / "workbench" / "control_plane" / "src"
    / "components" / "email" / "EmailToolCards.tsx"
)


def _lead(node: _ast.AST) -> str | None:
    if isinstance(node, _ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, _ast.JoinedStr):
        first = node.values[0]
        return str(first.value) if isinstance(first, _ast.Constant) else "{"
    if isinstance(node, _ast.Call) and node.args:
        return _lead(node.args[0])
    return None


def _returns(name: str) -> list[_ast.AST]:
    tree = _ast.parse(_AGENT.read_text(encoding="utf-8"))
    fn = next(
        n for n in _ast.walk(tree)
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == name
    )
    return [n.value for n in _ast.walk(fn) if isinstance(n, _ast.Return) and n.value is not None]


@pytest.mark.parametrize("tool", sorted(ACTING))
def test_each_answer_that_did_not_act_has_a_known_lead(tool: str) -> None:
    leads = [lead for v in _returns(tool) if (lead := _lead(v)) is not None]
    assert leads, f"{tool} has no literal answer"
    for lead in leads:
        assert lead.startswith(ACTING[tool]) or lead.startswith(NO_ACTION_LEADS), (
            f"{tool} answers {lead[:60]!r}, and the card would draw it as done"
        )


@pytest.mark.parametrize("helper", HELPERS)
def test_each_helper_answer_has_a_known_lead(helper: str) -> None:
    for v in _returns(helper):
        text = v.elts[1] if isinstance(v, _ast.Tuple) else v
        if isinstance(text, _ast.Constant) and text.value == "":
            continue  # it bound a mailbox, and the tool goes on
        lead = _lead(text)
        assert lead and lead.startswith(NO_ACTION_LEADS), (helper, lead)


def test_the_card_knows_each_lead() -> None:
    cards = _CARDS.read_text(encoding="utf-8")
    body = cards[cards.index("export function noActionOf("):]
    body = body[: body.index("\n}\n")]
    for ts in TS_LEADS:
        assert ts in body, f"noActionOf does not know {ts!r}"


# ── email-chat-no-mailbox (review round 1) ───────────────────────────────────

async def test_new_mail_with_no_mailbox_connected_sends_nothing(gw: Gateway) -> None:
    gw.accounts = []
    out = await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    assert out == "Not sent. No email accounts are connected."
    assert gw.writes == [] and gw.cards == []


@pytest.mark.parametrize(("tool", "kwargs"), RULE_TOOLS, ids=[t for t, _ in RULE_TOOLS])
async def test_no_mailbox_connected_changes_nothing(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    gw.accounts = []
    out = await getattr(agents, tool)(**kwargs)
    assert out == "Nothing changed. No email accounts are connected."
    assert gw.writes == [] and gw.cards == []


# ── email-chat-send-card-shows-hidden (review round 1) ───────────────────────

async def test_the_send_card_shows_each_bcc_and_each_file_before_a_long_subject(
    gw: Gateway,
) -> None:
    """A mail body can ask the model to add a hidden recipient or a file. The
    card shows both, even when a long subject from the sender of the mail fills
    the 500 characters that the card keeps."""
    gw.accounts = TWO[:1]
    await agents.send_email(
        body="Hi", to=["kim@contoso.test"], subject="S" * 600,
        bcc=["records@evil.test"], attachments=["outputs/payroll.xlsx"],
    )
    [card] = gw.cards
    shown = str(card["detail"]).strip()[:500]  # the cut of ask_tools.py
    assert "records@evil.test" in shown
    assert "outputs/payroll.xlsx" in shown
    assert shown.index("payroll.xlsx") < shown.index("Subject:")


# ── email-chat-binding-skips-separate (EM-T8g-1) ─────────────────────────────

def _separate(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    """Copies of ``rows``, each one kept separate."""
    return [{**r, "in_all_inboxes": False} for r in rows]


@pytest.mark.parametrize(("tool", "kwargs"), RULE_TOOLS, ids=[t for t, _ in RULE_TOOLS])
async def test_the_rule_question_leaves_out_a_separate_mailbox(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    gw.accounts = [*TWO, NDA]
    out = await getattr(agents, tool)(**kwargs)
    assert out.startswith("Which mailbox?")
    assert f"• {A_TEXT} (account_id {BOX_A})" in out
    assert f"• {B_TEXT} (account_id {BOX_B})" in out
    assert BOX_C not in out and C_TEXT not in out
    assert gw.writes == [] and gw.cards == []


@pytest.mark.parametrize(("tool", "kwargs"), RULE_TOOLS, ids=[t for t, _ in RULE_TOOLS])
async def test_one_pooled_mailbox_and_a_separate_one_asks(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    """Review round 1, P1: Work in All inboxes and a separate NDA mailbox.
    A chat in the scope of NDA sends no ``account_id``. The tool must ask,
    and list both, and never bind Work. Before the fix, ``save_knowledge``
    wrote an NDA fact into Work, and ``install_default_rules(reset=True)``
    showed a card with no mailbox and then deleted the rules of Work."""
    gw.accounts = [NDA, TWO[0]]
    out = await getattr(agents, tool)(**kwargs)
    assert out.startswith("Which mailbox?")
    assert f"• {A_TEXT} (account_id {BOX_A})" in out
    assert f"• {C_TEXT} (account_id {BOX_C})" in out
    assert gw.writes == [] and gw.cards == []


async def test_with_no_pooled_mailbox_the_full_list_stays(gw: Gateway) -> None:
    gw.accounts = _separate(*TWO)
    out = await agents.create_rule(name="Bank")
    assert out.startswith("Which mailbox?")
    assert f"• {A_TEXT} (account_id {BOX_A})" in out
    assert f"• {B_TEXT} (account_id {BOX_B})" in out
    # One mailbox that is separate is still the mailbox of the member.
    gw.accounts = _separate(TWO[1])
    await agents.create_rule(name="Bank")
    assert gw.writes and all(BOX_B in f"{p} {b}" for _v, p, b in gw.writes)


async def test_a_sent_from_answer_that_names_a_separate_mailbox_is_no_answer(
    gw: Gateway,
) -> None:
    gw.accounts = [*TWO, NDA]
    gw.sent_from = {"kim@contoso.test": BOX_C}
    out = await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    assert out.startswith("Send from which mailbox?")
    assert gw.writes == [] and gw.cards == []
    assert f"• {A_TEXT} (account_id {BOX_A})" in out
    assert f"• {B_TEXT} (account_id {BOX_B})" in out
    assert BOX_C not in out and C_TEXT not in out


async def test_a_sent_from_answer_that_names_a_pooled_mailbox_binds(
    gw: Gateway,
) -> None:
    gw.accounts = [*TWO, NDA]
    gw.sent_from = {"kim@contoso.test": BOX_B}
    await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    [sent] = _sends(gw)
    assert sent["account_id"] == BOX_B


@pytest.mark.parametrize("answer", [BOX_A, BOX_C, None], ids=["pooled", "separate", "none"])
async def test_new_mail_with_one_pooled_mailbox_and_a_separate_one_asks(
    gw: Gateway, answer: str | None,
) -> None:
    """Review round 1, P1: with fewer than two mailboxes in All inboxes, no
    chat is in All inboxes. So new mail with no ``account_id`` asks, lists
    each mailbox, and does not read ``sent-from``."""
    gw.accounts = [NDA, TWO[0]]
    gw.sent_from = {"kim@contoso.test": answer} if answer else {}
    out = await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    assert out.startswith("Send from which mailbox?")
    assert f"• {A_TEXT} (account_id {BOX_A})" in out
    assert f"• {C_TEXT} (account_id {BOX_C})" in out
    assert gw.writes == [] and gw.cards == []
    assert all(p != "/email/contacts/sent-from" for p, _ in gw.gets)


async def test_with_no_pooled_mailbox_sent_from_still_binds_no_separate_one(
    gw: Gateway,
) -> None:
    """Strict: each mailbox is separate, so no answer of ``sent-from`` binds,
    and the question lists them all."""
    gw.accounts = _separate(*TWO)
    gw.sent_from = {"kim@contoso.test": BOX_B}
    out = await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    assert out.startswith("Send from which mailbox?")
    assert f"• {A_TEXT} (account_id {BOX_A})" in out
    assert f"• {B_TEXT} (account_id {BOX_B})" in out
    assert gw.writes == [] and gw.cards == []


async def test_a_row_with_no_field_is_in_all_inboxes(gw: Gateway) -> None:
    """An answer from before migration 229 has no ``in_all_inboxes``. Its
    mailbox is in All inboxes, as the column default says. So two such rows
    are two pooled mailboxes, and ``sent-from`` can bind one of them."""
    old = [{k: v for k, v in r.items() if k != "in_all_inboxes"} for r in TWO]
    gw.accounts = [*old, NDA]
    gw.sent_from = {"kim@contoso.test": BOX_B}
    await agents.send_email(body="Hi", to=["kim@contoso.test"], subject="S")
    [sent] = _sends(gw)
    assert sent["account_id"] == BOX_B


async def test_a_named_separate_mailbox_still_acts(gw: Gateway) -> None:
    """A tool that names the separate mailbox acts in it (D-EM-30)."""
    gw.accounts = [*TWO, NDA]
    out = await agents.create_rule(BOX_C, name="Bank")
    assert gw.writes and all(BOX_C in f"{p} {b}" for _v, p, b in gw.writes)
    assert out.endswith(f"in {C_TEXT}.")


# ── email-chat-write-names-mailbox (EM-T8g-1 review round 1) ────────────────

@pytest.mark.parametrize(("tool", "kwargs"), RULE_TOOLS, ids=[t for t, _ in RULE_TOOLS])
async def test_each_write_result_names_the_mailbox(
    gw: Gateway, tool: str, kwargs: dict[str, Any],
) -> None:
    """Each answer of an item 3 tool names the mailbox that it changed."""
    gw.accounts = [*TWO, NDA]
    out = await getattr(agents, tool)(BOX_C, **kwargs)
    assert C_TEXT in out, out
    assert A_TEXT not in out and B_TEXT not in out


async def test_the_reset_card_names_the_mailbox(gw: Gateway) -> None:
    gw.accounts = [*TWO, NDA]
    out = await agents.install_default_rules(BOX_C, reset=True)
    [card] = gw.cards
    assert C_TEXT in card["title"]
    assert str(card["detail"]).startswith(f"Mailbox: {C_TEXT}.")
    assert out.startswith(f"Reset rules in {C_TEXT}:")


async def test_a_reset_of_an_id_of_no_mailbox_changes_nothing(gw: Gateway) -> None:
    gw.accounts = [*TWO, NDA]
    out = await agents.install_default_rules("0d0d0d0d-0000-4000-8000-00000000000d", reset=True)
    assert out.startswith("Nothing changed.")
    assert gw.writes == [] and gw.cards == []


# ── email-chat-list-accounts-separate (EM-T8g-1 review round 1) ─────────────

async def test_list_accounts_marks_a_separate_mailbox_and_leaves_it_out_of_the_total(
    gw: Gateway,
) -> None:
    gw.accounts = [*TWO, NDA]
    out = await agents.list_accounts()
    # 2 + 1 unread in All inboxes. The 4 of the separate mailbox stay apart.
    assert out.startswith("Connected accounts (3 unread total):")
    assert f"• {A_TEXT} — id={BOX_A}, 2 unread" in out
    assert f"• {C_TEXT} (separate) — id={BOX_C}, 4 unread" in out
    assert f"{A_TEXT} (separate)" not in out
    assert "leaves out each separate mailbox" in out
    # With no separate mailbox, the answer does not change.
    gw.accounts = list(TWO)
    plain = await agents.list_accounts()
    assert "(separate)" not in plain and "leaves out" not in plain
    assert plain.startswith("Connected accounts (3 unread total):")
