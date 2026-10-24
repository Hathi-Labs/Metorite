"""H-391 — an email write from WhatsApp, approved with a reply code. Database-free.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §18 and
``email_app_master_plan.md`` §21.13. The owner rule of 2026-10-10: a channel
changes delivery, never function.

The REAL card gate (``ask_tools.request_confirmation``), the REAL park and
approval (``acb_skills.whatsapp_acts``), the REAL email tools
(``compose_reply``, ``send_draft`` in a held run, ``manage_inbox``), the
REAL record of an answer (``reply_card.record_reply_answer`` and
``take_reply_answer``, over a fake Redis under the real tenant wrapper) and
the REAL settle of ``whatsapp_channel/acts.py`` run here. The gateway routes
of the email tools and the act table are fakes. No test sends a mail.

R7 fences named here:

* ``h391-summary-whole``: the summary holds the act, each person with a
  number and a class mark, the subject and the whole text. A long text is
  split over parts and never cut. The code is in the last part only.
* ``h391-code-bound``: only the right code, from this phone, while the act
  lives, approves. A wrong code, an expired code, a reused code, a quoted
  reply, a pasted summary and a reply from another phone do nothing.
* ``h391-drop-and-include``: WITHOUT drops a line, and a new address gets
  nothing unless WITH names it.
* ``h391-sends-the-record``: an approval sends exactly the recorded rows
  (minus the dropped ones), through the same act route as the web card.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from acb_skills import ask_tools
from acb_skills import reply_card as rcard
from acb_skills import whatsapp_acts as wacts
from acb_skills import whatsapp_ui as wui

REPO = Path(__file__).resolve().parents[2]
_AGENT = REPO / "apps" / "agents" / "agent-email-assistant" / "agents.py"

ORG = "0f8fad5b-d9cb-469f-a165-70867728950e"
MEMBER = "jane@acme.test"
THREAD = "wa-thread-1"
MAIL = "0b0b0b0b-0000-4000-8000-00000000000b"
BOX = "acc-1"
CARD_ID = "a" * 32


def _load_email():
    spec = importlib.util.spec_from_file_location("ea_wac_email_confirm", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_email()


# ── The fakes ───────────────────────────────────────────────────────────────


class _Redis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    async def set(self, k: str, v: str, nx: bool = False, ex: int | None = None) -> bool:
        if nx and k in self.keys:
            return False
        self.keys[k] = v
        return True

    async def getdel(self, k: str) -> str | None:
        return self.keys.pop(k, None)


#: The rows of a held send card, as the gateway builds them (§21.4).
ROWS = [
    {"id": "r1", "label": "To: kim@contoso.test", "kind": "to", "class": "thread",
     "checked": True},
    {"id": "r2", "label": "Cc: lee@acme.test", "kind": "cc", "class": "org",
     "checked": True},
    {"id": "r3", "label": "Cc: kiran@fracktai.in", "kind": "cc", "class": "new",
     "checked": False,
     "warning": "the domain looks like fracktal.in, which you know — check each letter"},
]


class _Gateway:
    """The gateway routes of the email tools. The two act routes check the
    answer against the record, as the real routes do (``take_reply_answer``),
    and send only the recorded rows that the answer ticks."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.sent: list[dict[str, Any]] = []

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        if path == "/email/reply-card":
            return {
                "account_id": BOX, "mailbox": f"Work · {MEMBER}", "subject": "Re: Quote",
                "original": {"from": "Kim <kim@contoso.test>", "subject": "Quote",
                             "received": "", "excerpt": "Can you confirm?"},
                "recipients": {"reply": {"to": ["kim@contoso.test"], "cc": []},
                               "reply_all": {"to": ["kim@contoso.test"],
                                             "cc": ["lee@acme.test", "ops@contoso.test"]}},
                "reply_all": True,
                "body": body.get("draft") or "Hi Kim,\n\nYes, Friday works.\n\nJane",
                "message_id": MAIL, "notices": [],
            }
        if path == "/email/reply-card/act":
            ok = await rcard.take_reply_answer(ORG, body["card_request_id"], MEMBER,
                                               body["answer"], body["card_thread_id"],
                                               wait_s=0)
            assert ok, "the act route found no record of this answer"
            choice = rcard.parse_reply_answer(body["answer"])
            self.sent.append({"to": list(choice.to), "cc": list(choice.cc),
                              "body": choice.body})
            return {"ok": True, "action": "send", "to": list(choice.to),
                    "cc": list(choice.cc), "mailbox": f"Work · {MEMBER}"}
        if path == "/email/held-card":
            return {"title": "Send this draft?", "detail": f"From Work · {MEMBER}",
                    "context": "Subject: Quote\n\nHi Kim, the quote is attached.",
                    "rows": [dict(r) for r in ROWS], "card_id": CARD_ID}
        if path == "/email/held-card/act":
            ok = await rcard.take_reply_answer(ORG, body["card_id"], MEMBER,
                                               body["answer"], body["card_thread_id"],
                                               wait_s=0)
            assert ok, "the act route found no record of this answer"
            ticked = ask_tools.ticked_rows(body["answer"],
                                           frozenset(r["id"] for r in ROWS))
            assert ticked, "a forged or empty answer"
            rows = [r for r in ROWS if r["id"] in ticked]
            self.sent.append({"rows": sorted(ticked)})
            return {"to": [r["label"][4:] for r in rows if r["kind"] == "to"],
                    "cc": [r["label"][4:] for r in rows if r["kind"] == "cc"]}
        if path == "/email/messages/bulk":
            self.sent.append(dict(body))
            return {"affected": len(body["message_ids"])}
        if path == "/email/messages/summaries":
            return {"summaries": [{"from": "Kim", "subject": "Quote"}]}
        raise AssertionError(f"unexpected POST {path}")


@pytest.fixture()
def gw(monkeypatch) -> _Gateway:
    from acb_common import get_settings
    from acb_common.tenant_redis import TenantRedis

    g = _Gateway()
    monkeypatch.setattr(agents, "_post", g.post)
    monkeypatch.setattr(agents, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)

    async def accounts() -> list[dict[str, Any]]:
        return [{"id": BOX, "email_address": MEMBER, "label": "Work"}]

    monkeypatch.setattr(agents, "_accounts", accounts)
    monkeypatch.setattr(wacts, "_run_agent", lambda: wacts.EMAIL_AGENT)
    store = _Redis()
    monkeypatch.setattr(rcard, "_tenant_redis", lambda: TenantRedis(store))
    # §21: a held card's answer is recorded only with the switch on.
    monkeypatch.setattr(get_settings(), "email_held_card_send", True)
    return g


async def _call(fn: Any, kwargs: dict[str, Any]) -> Any:
    """One tool call, named as the injection seam names it (WAC-4)."""
    with wacts.tool_call(fn, (), kwargs):
        return await fn(**kwargs)


async def _park(fn: Any, kwargs: dict[str, Any]) -> tuple[str, wacts.ParkedAct]:
    with wui.whatsapp_run("orchestrator", writes=True) as run:
        out = await _call(fn, kwargs)
    assert run.parked is not None, out
    return out, run.parked


async def _approve(act: wacts.ParkedAct, *, without: frozenset[int] = frozenset(),
                   include: frozenset[int] = frozenset(), call: dict[str, Any] | None = None,
                   fn: Any = None) -> tuple[str, Any, Any]:
    """The approval run inside the yes of the code. The run makes the stored
    call, or *fn* with *call* when a test makes another call."""
    fn = fn or getattr(agents, act.tool)
    with wui.whatsapp_run("orchestrator", writes=True) as run, wacts.approving_email(
            act.card, tool=act.tool, arguments=act.arguments, without=without,
            include=include, org=ORG, member=MEMBER, thread=THREAD) as yes:
        out = await _call(fn, dict(act.arguments if call is None else call))
    return out, yes, run


# ── The park ────────────────────────────────────────────────────────────────


async def test_a_reply_parks_with_its_text_pinned_and_sends_nothing(gw) -> None:
    out, act = await _park(agents.compose_reply,
                           {"email_id": MAIL, "account_id": BOX,
                            "instruction": "say Friday works"})
    assert out == wacts.EMAIL_PARKED_TEXT
    assert act.kind == "code" and act.reply is not None
    # The approval run shows the SAME text, so the server writes no new one.
    assert act.arguments["draft"] == "Hi Kim,\n\nYes, Friday works.\n\nJane"
    assert act.arguments["reply_all"] is True
    assert [p for p, _ in gw.posts] == ["/email/reply-card"]
    assert gw.sent == []


async def test_an_archive_parks_and_writes_nothing(gw) -> None:
    out, act = await _park(agents.manage_inbox,
                           {"action": "archive", "message_ids": [MAIL]})
    assert out == wacts.EMAIL_PARKED_TEXT
    assert act.tool == "manage_inbox" and act.rows is None and act.reply is None
    assert gw.sent == []


async def test_a_tool_of_another_agent_never_parks(gw, monkeypatch) -> None:
    monkeypatch.setattr(wacts, "_run_agent", lambda: "orchestrator")
    with wui.whatsapp_run("orchestrator", writes=True) as run:
        out = await _call(agents.manage_inbox, {"action": "archive", "message_ids": [MAIL]})
    assert run.parked is None
    assert out == agents._NO_CARD_CHANNEL and gw.sent == []


async def test_with_writes_off_nothing_parks(gw) -> None:
    with wui.whatsapp_run("orchestrator", writes=False) as run:
        out = await _call(agents.manage_inbox, {"action": "archive", "message_ids": [MAIL]})
    assert run.parked is None and out == agents._NO_CARD_CHANNEL


async def test_one_act_waits_at_a_time(gw) -> None:
    with wui.whatsapp_run("orchestrator", writes=True) as run:
        first = await _call(agents.manage_inbox, {"action": "archive", "message_ids": [MAIL]})
        second = await _call(agents.manage_inbox, {"action": "read", "message_ids": [MAIL]})
    assert first == wacts.EMAIL_PARKED_TEXT
    assert second == wacts.EMAIL_WAITING_TEXT
    assert run.parked.arguments["action"] == "archive"


# ── The summary ─────────────────────────────────────────────────────────────


def _held_act(body: str = "Hi Kim, the quote is attached.") -> wacts.ParkedAct:
    return wacts.ParkedAct(
        tool="send_draft", arguments={"account_id": BOX, "draft_id": "d1"},
        card="x", title="Send this draft?", detail=f"From Work · {MEMBER}",
        context=f"Subject: Quote\n\n{body}", kind="code", rows=[dict(r) for r in ROWS])


def test_the_summary_holds_every_person_with_a_number_and_a_mark() -> None:
    from acb_skills.held_card import NEW_MARK, THREAD_MARK

    text = wacts.code_summary(_held_act(), "7K3Q")
    assert "*Send this draft?*" in text and f"From Work · {MEMBER}" in text
    assert f"1. To: kim@contoso.test ({THREAD_MARK})" in text
    assert "2. Cc: lee@acme.test" in text
    assert f"3. Cc: kiran@fracktai.in ({NEW_MARK})" in text
    assert f"[{wacts.UNTICKED_NOTE}]" in text.split("3. ")[1].splitlines()[0]
    assert "WARNING: the domain looks like fracktal.in" in text
    assert "Subject: Quote" in text and "Hi Kim, the quote is attached." in text
    assert text.rstrip().endswith("Type it as a new message.")
    assert "YES 7K3Q WITHOUT 2" in text and "YES 7K3Q WITH 3" in text


def test_a_reply_summary_shows_the_text_and_each_person() -> None:
    form = ask_tools.clean_reply_form({
        "message_id": MAIL, "mailbox": "Work", "subject": "Re: Quote",
        "original": {"from": "Kim", "subject": "Quote"},
        "recipients": {"reply": {"to": ["kim@contoso.test"], "cc": []},
                       "reply_all": {"to": ["kim@contoso.test"], "cc": ["x@new.example"]}},
        "reply_all": True, "body": "Hi Kim,\n\nYes.",
        "classes": {"kim@contoso.test": "thread", "x@new.example": "new"},
        "ticked": {"reply_all": {"to": ["kim@contoso.test"], "cc": []}},
    })
    act = wacts.ParkedAct(tool="compose_reply", arguments={}, card="x",
                          title="Reply to kim@contoso.test?", detail="", context="",
                          kind="code", reply=form)
    text = wacts.code_summary(act, "7K3Q")
    assert "1. To: kim@contoso.test" in text
    assert "2. Cc: x@new.example" in text and wacts.UNTICKED_NOTE in text
    assert "Text:\nHi Kim,\n\nYes." in text and "Reply all" in text


def test_a_long_text_splits_over_parts_and_is_never_cut() -> None:
    body = " ".join(f"word{i}" for i in range(2400))  # about 19 000 characters
    act = _held_act(body)
    parts = wacts.code_summary_parts(act, "7K3Q", 4096)
    assert len(parts) >= 4 and all(len(p) <= 4096 for p in parts)
    bodies = [p.split("\n", 1)[1] for p in parts]
    joined = "".join(bodies)
    assert body in joined
    # The code is in the last part only.
    assert all("7K3Q" not in b for b in bodies[:-1]) and "YES 7K3Q" in bodies[-1]
    assert parts[0].startswith(f"(1/{len(parts)})")


# ── The member's reply ──────────────────────────────────────────────────────


@pytest.mark.parametrize(("text", "yes", "code", "without", "include", "plain"), [
    ("YES 7K3Q", True, "7K3Q", set(), set(), True),
    ("yes 7k3q", True, "7K3Q", set(), set(), True),
    ("send 7K3Q without 2", True, "7K3Q", {2}, set(), True),
    ("YES 7K3Q WITHOUT 2, 4 WITH 3", True, "7K3Q", {2, 4}, {3}, True),
    ("YES 7K3Q with 3 and 5.", True, "7K3Q", set(), {3, 5}, True),
    ("YES", True, "", set(), set(), True),
    ("NO", False, "", set(), set(), True),
    ("YES 7K3Q please send it", True, "7K3Q", set(), set(), False),
    ("YES 7K3Q 7K3Q", True, "7K3Q", set(), set(), False),
])
def test_the_reply_grammar(text, yes, code, without, include, plain) -> None:
    got = wacts.parse_code_answer(text)
    assert got is not None
    assert (got.yes, got.code, set(got.without), set(got.include), got.plain) == (
        yes, code, without, include, plain)


@pytest.mark.parametrize("text", ["Thanks!", "*Send this draft?* ... Reply YES 7K3Q", ""])
def test_any_other_message_is_no_reply(text) -> None:
    assert wacts.parse_code_answer(text) is None


# ── The approval: the same act path as the web card ─────────────────────────


async def test_an_approved_reply_sends_to_the_kept_people_through_the_act_route(gw) -> None:
    _out, act = await _park(agents.compose_reply, {"email_id": MAIL, "account_id": BOX})
    # Line 3 is ops@contoso.test. The member leaves it out.
    out, yes, run = await _approve(act, without=frozenset({3}))
    assert yes.used and run.parked is None
    assert gw.sent == [{"to": ["kim@contoso.test"], "cc": ["lee@acme.test"],
                        "body": "Hi Kim,\n\nYes, Friday works.\n\nJane"}]
    assert out.startswith("Sent to kim@contoso.test, with a copy to lee@acme.test")
    # The approval run sent the pinned text: no new text was written.
    card_posts = [b for p, b in gw.posts if p == "/email/reply-card"]
    assert card_posts[-1]["draft"] == "Hi Kim,\n\nYes, Friday works.\n\nJane"


async def test_a_held_send_starts_without_the_new_address(gw, monkeypatch) -> None:
    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    _out, act = await _park(agents.send_draft, {"account_id": BOX, "draft_id": "d1"})
    assert act.rows is not None and [r["id"] for r in act.rows] == ["r1", "r2", "r3"]
    out, yes, _run = await _approve(act)
    assert yes.used
    # Only the rows that start ticked: the new address gets nothing.
    assert gw.sent == [{"rows": ["r1", "r2"]}]
    assert "kiran@fracktai.in" not in out


async def test_a_held_send_includes_a_new_address_only_when_named(gw, monkeypatch) -> None:
    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    _out, act = await _park(agents.send_draft, {"account_id": BOX, "draft_id": "d1"})
    await _approve(act, without=frozenset({2}), include=frozenset({3}))
    assert gw.sent == [{"rows": ["r1", "r3"]}]


async def test_an_approved_archive_runs_once(gw) -> None:
    _out, act = await _park(agents.manage_inbox, {"action": "archive", "message_ids": [MAIL]})
    out, yes, _run = await _approve(act)
    assert yes.used and out == "archive: affected 1 message(s)."
    assert gw.sent == [{"action": "archive", "message_ids": [MAIL]}]


async def test_a_card_that_changed_gets_no_yes_and_parks_again(gw) -> None:
    _out, act = await _park(agents.manage_inbox, {"action": "archive", "message_ids": [MAIL]})
    changed = wacts.ParkedAct(**{**act.__dict__, "arguments": {
        "action": "trash", "message_ids": [MAIL]}})
    out, yes, run = await _approve(changed)
    assert not yes.used and gw.sent == []
    assert out == wacts.EMAIL_PARKED_TEXT and run.parked.arguments["action"] == "trash"


async def test_the_yes_answers_one_card_only(gw) -> None:
    _out, act = await _park(agents.manage_inbox, {"action": "archive", "message_ids": [MAIL]})
    fn = agents.manage_inbox
    with wui.whatsapp_run("orchestrator", writes=True), wacts.approving_email(
            act.card, tool=act.tool, arguments=act.arguments, org=ORG, member=MEMBER,
            thread=THREAD):
        await _call(fn, dict(act.arguments))
        again = await _call(fn, dict(act.arguments))
    assert len(gw.sent) == 1
    assert again == wacts.EMAIL_PARKED_TEXT


async def test_a_failed_record_sends_nothing(gw, monkeypatch) -> None:
    async def no_record(*_a: Any, **_k: Any) -> bool:
        return False

    _out, act = await _park(agents.compose_reply, {"email_id": MAIL, "account_id": BOX})
    monkeypatch.setattr(rcard, "record_reply_answer", no_record)
    out, yes, _run = await _approve(act)
    assert yes.used and gw.sent == []
    assert "/email/reply-card/act" not in [p for p, _ in gw.posts]
    assert out.startswith("Nothing") or out == agents._NO_CARD_CHANNEL


# ── The settle: the code, the phone, the expiry ─────────────────────────────

from gateway.routes.whatsapp_channel import acts, bot_run  # noqa: E402

PHONE = "919990000001"
OTHER_PHONE = "919990000002"


def _req(phone: str = PHONE) -> bot_run.RunRequest:
    return bot_run.RunRequest(message_id="m-1", organization_id=ORG, member_email=MEMBER,
                              wa_id=phone, wamid="w-1", chat_session_id=THREAD)


class _Acts:
    """The act table of one phone, in memory."""

    def __init__(self, code: str = "7K3Q", rows: list[dict[str, Any]] | None = None) -> None:
        card = wacts.email_card_text("Send this draft?", "", "", rows or ROWS, None)
        self.row: dict[str, Any] = {
            "id": "act-1", "member_email": MEMBER, "link_id": "link-1",
            "tool_name": "send_draft", "arguments": {"account_id": BOX, "draft_id": "d1"},
            "card_text": card, "live": True, "offered": True, "offered_wamid": "out-1",
            "confirm_code_hash": acts.code_hash(code), "tap_of": None,
            "after_offer": True, "state": "pending", "wa_id": PHONE,
        }
        self.closed: list[tuple[str, str, str | None]] = []

    async def live(self, req: Any) -> dict[str, Any] | None:
        row = self.row
        return dict(row) if row["state"] == "pending" and req.wa_id == row["wa_id"] else None

    async def close(self, act_id: str, frm: str, to: str, code: str | None) -> bool:
        self.closed.append((frm, to, code))
        self.row["state"] = to
        return True

    async def claim(self, act_id: str) -> bool:
        if self.row["state"] != "pending":
            return False
        self.row["state"] = "running"
        return True


@pytest.fixture()
def table(monkeypatch) -> _Acts:
    t = _Acts()
    monkeypatch.setattr(acts, "_live_act", t.live)
    monkeypatch.setattr(acts, "_close", t.close)
    monkeypatch.setattr(acts, "_claim", t.claim)

    async def link(_wa: str, _email: str) -> str:
        return "link-1"

    monkeypatch.setattr(acts, "_current_link_id", link)
    return t


async def test_the_right_code_from_this_phone_approves(table) -> None:
    out = await acts.settle(_req(), "YES 7K3Q WITHOUT 2 WITH 3")
    assert isinstance(out, acts.Approved) and out.kind == "email"
    assert (out.without, out.include) == (frozenset({2}), frozenset({3}))
    assert out.card == table.row["card_text"] and table.row["state"] == "running"


async def test_a_wrong_code_does_nothing_and_says_why(table) -> None:
    assert await acts.settle(_req(), "YES ABCD") == acts.REPLY_WRONG_CODE
    assert table.row["state"] == "pending" and table.closed == []


async def test_an_expired_code_closes_the_act(table) -> None:
    table.row["live"] = False
    assert await acts.settle(_req(), "YES 7K3Q") == acts.REPLY_CODE_EXPIRED
    assert table.row["state"] == "expired"


async def test_a_reused_code_does_nothing(table) -> None:
    assert isinstance(await acts.settle(_req(), "YES 7K3Q"), acts.Approved)
    assert await acts.settle(_req(), "YES 7K3Q") == acts.REPLY_NO_ACT


async def test_a_code_from_another_phone_does_nothing(table) -> None:
    assert await acts.settle(_req(OTHER_PHONE), "YES 7K3Q") == acts.REPLY_NO_ACT
    assert table.row["state"] == "pending"


async def test_a_quoted_reply_does_nothing(table) -> None:
    table.row["tap_of"] = "out-1"
    assert await acts.settle(_req(), "YES 7K3Q") == acts.REPLY_QUOTED
    assert table.row["state"] == "pending"


async def test_a_pasted_summary_with_the_code_does_nothing(table) -> None:
    text = "*Send this draft?*\n1. To: kim@contoso.test\nReply YES 7K3Q to approve this"
    assert await acts.settle(_req(), text) == acts.REPLY_NOT_PLAIN
    assert table.row["state"] == "pending"


async def test_a_reply_with_no_code_or_extra_words_does_nothing(table) -> None:
    assert await acts.settle(_req(), "YES") == acts.REPLY_NO_CODE
    assert await acts.settle(_req(), "YES 7K3Q and also bcc x@evil.example") \
        == acts.REPLY_NOT_PLAIN
    assert table.row["state"] == "pending"


async def test_no_cancels(table) -> None:
    assert await acts.settle(_req(), "no") == acts.REPLY_CANCELLED
    assert table.row["state"] == "cancelled"


@pytest.mark.parametrize(("text", "reply"), [
    ("YES 7K3Q WITHOUT 9", acts.REPLY_BAD_NUMBER),
    ("YES 7K3Q WITHOUT 2 WITH 2", acts.REPLY_BAD_NUMBER),
    ("YES 7K3Q WITHOUT 1", acts.REPLY_NOBODY),
])
async def test_a_number_that_names_no_line_does_nothing(table, text, reply) -> None:
    assert await acts.settle(_req(), text) == reply
    assert table.row["state"] == "pending"


async def test_numbers_on_a_card_with_no_list_do_nothing(table) -> None:
    table.row["card_text"] = wacts.email_card_text("Archive 1 message?", "", "", None, None)
    assert await acts.settle(_req(), "YES 7K3Q WITHOUT 1") == acts.REPLY_NO_LIST


async def test_another_message_voids_the_act_and_runs_as_normal(table) -> None:
    assert await acts.settle(_req(), "What is on my calendar?") is None
    assert table.row["state"] == "void"


async def test_a_changed_link_voids_the_act(table, monkeypatch) -> None:
    async def other(_wa: str, _email: str) -> str:
        return "link-2"

    monkeypatch.setattr(acts, "_current_link_id", other)
    assert await acts.settle(_req(), "YES 7K3Q") == acts.REPLY_LINK
    assert table.row["state"] == "void"


def test_the_code_is_short_random_and_stored_as_a_hash() -> None:
    codes = {acts.new_code() for _ in range(50)}
    assert len(codes) > 40
    for code in codes:
        assert len(code) == wacts.CODE_LENGTH and set(code) <= set(wacts.CODE_ALPHABET)
        assert acts.code_hash(code) == acts.code_hash(code.lower()) != code
        assert len(acts.code_hash(code)) == 64


# ── The offer and the inbound message ───────────────────────────────────────


async def test_the_offer_is_text_only_and_the_last_part_names_the_act(monkeypatch) -> None:
    async def park_code(_req: Any, _act: Any) -> tuple[str, str]:
        return "act-9", "7K3Q"

    monkeypatch.setattr(acts, "park_code", park_code)
    act = _held_act(" ".join(f"word{i}" for i in range(1500)))
    parts = await bot_run._offer_act(_req(), act)
    assert len(parts) >= 2
    assert all(p.kind == "text" and p.interactive is None for p in parts)
    assert [p.act_id for p in parts] == [None] * (len(parts) - 1) + ["act-9"]
    assert "YES 7K3Q" in parts[-1].rendition


def test_a_forwarded_answer_is_marked() -> None:
    from gateway.routes.whatsapp_channel import inbound

    assert inbound.is_forwarded({"context": {"forwarded": True}})
    assert inbound.is_forwarded({"context": {"frequently_forwarded": True}})
    assert not inbound.is_forwarded({"context": {"id": "wamid.1"}})
    assert not inbound.is_forwarded({})


def test_the_approval_turn_carries_no_member_text() -> None:
    approved = acts.Approved(act_id="a", tool="send_draft", arguments={"draft_id": "d1"},
                             card="c", kind="email")
    turn = acts.approved_message("YES 7K3Q WITHOUT 2", approved)
    assert "7K3Q" not in turn and "send_draft" in turn
    assert json.dumps({"draft_id": "d1"}) in turn


async def test_a_card_that_the_gate_cuts_never_parks(gw) -> None:
    """A summary never shows less than the act sends (§21): a card whose
    context the gate cuts does not park, and the tool says why."""

    async def send_email(**_kw: Any) -> str:  # the name of an email act
        ok = await ask_tools.request_confirmation(
            title="Send this email?", detail="To kim@contoso.test",
            context="To: kim@contoso.test\n\n" + "x" * 5000)
        return "sent" if ok else (agents._wa_parked() or "no")

    with wui.whatsapp_run("orchestrator", writes=True) as run:
        out = await _call(send_email, {"body": "x"})
    assert run.parked is None
    assert out == wacts.EMAIL_TOO_LONG_TEXT


# ── The yes binds the call, not only the text (review P1) ───────────────────


def _ids(n: int, start: int = 0) -> list[str]:
    return [f"0b0b0b0b-0000-4000-8000-{i:012d}" for i in range(start, start + n)]


async def test_same_text_with_other_message_ids_gets_no_yes(gw) -> None:
    """A trash of 25 shows 20 lines and a count. Five swapped ids give the
    same card text, and must get no yes."""
    shown = _ids(25)
    _out, act = await _park(agents.manage_inbox, {"action": "trash", "message_ids": shown})
    swapped = shown[:20] + _ids(5, start=100)
    out, yes, run = await _approve(act, call={"action": "trash", "message_ids": swapped})
    assert not yes.used and gw.sent == []
    assert out == wacts.EMAIL_PARKED_TEXT and run.parked.arguments["message_ids"] == swapped


async def test_another_tool_with_the_same_text_gets_no_yes(gw) -> None:
    async def send_draft(**_kw: Any) -> str:
        ok = await ask_tools.request_confirmation(title="Same card", detail="d", context="c")
        return "sent" if ok else (agents._wa_parked() or "no")

    async def schedule_send(**_kw: Any) -> str:
        ok = await ask_tools.request_confirmation(title="Same card", detail="d", context="c")
        return "sent" if ok else (agents._wa_parked() or "no")

    _out, act = await _park(send_draft, {"account_id": BOX, "draft_id": "d1"})
    out, yes, _run = await _approve(act, fn=schedule_send)
    assert not yes.used and out == wacts.EMAIL_PARKED_TEXT


@pytest.mark.parametrize("changed", [{"draft_id": "d2"}, {"account_id": "acc-2"}])
async def test_a_held_send_of_another_draft_gets_no_yes(gw, monkeypatch, changed) -> None:
    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    _out, act = await _park(agents.send_draft, {"account_id": BOX, "draft_id": "d1"})
    out, yes, _run = await _approve(act, call={**act.arguments, **changed})
    assert not yes.used and gw.sent == []
    assert out == wacts.EMAIL_PARKED_TEXT


async def test_the_honest_call_gets_the_yes(gw) -> None:
    shown = _ids(25)
    _out, act = await _park(agents.manage_inbox, {"action": "trash", "message_ids": shown})
    _out, yes, _run = await _approve(act)
    assert yes.used and gw.sent == [{"action": "trash", "message_ids": shown}]
