"""EM-CR: one email reply, confirmed and done in the chat.

Spec ``project-docs/specs/email_app_master_plan.md`` §18. Owner request of
2026-10-20: read an email with no permission prompt, then confirm ONE reply
card, and the reply completes with no Email app opened.

What this file fences (R7). Each rule names the test that fails:

1. The gateway derives the recipients. Reply goes to the Reply-To, else to
   the sender. Reply all adds each To and Cc. The member's own addresses
   never get the reply. ``test_reply_*``, ``test_reply_all_*``.
2. The body that leaves is the body the member posted. The gateway sends an
   answer only when the record that ``/agent/respond-input`` wrote matches,
   once. ``test_the_act_*``, ``test_a_model_*``.
3. ``compose_reply`` names no recipient and no file. Its ``draft`` only
   fills the card, and the held run keeps the tool only while its
   parameters are the pinned set.
   ``test_the_parameter_fence_*``, ``test_a_held_run_*``.
4. With no card, the tool drafts, saves and sends nothing.
   ``test_no_card_*``.

The mail provider and the gateway are fakes. No test sends a mail.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import types
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from acb_skills import egress as eg
from acb_skills import reply_card as rcard

REPO = Path(__file__).resolve().parents[2]
_AGENT = REPO / "apps" / "agents" / "agent-email-assistant" / "agents.py"

ORG = "org-reply-1"
MEMBER = "jane@acme.test"
MAIL = "0b0b0b0b-0000-4000-8000-00000000000b"
BOX = "acc-1"


def _load_email():
    spec = importlib.util.spec_from_file_location("ea_chat_reply", _AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load_email()


def _answer(action: str = "send", *, reply_all: bool = False, body: str = "Hi Kim,\n\nYes.",
            to: list[str] | None = None, cc: list[str] | None = None, **extra: Any) -> str:
    reply = {"action": action, "reply_all": reply_all, "body": body,
             "to": to if to is not None else ["kim@contoso.test"], "cc": cc or [],
             "message_id": MAIL}
    reply.update(extra)
    return "APPROVE " + json.dumps({"reply": reply})


#: The thread that owns the card: the key of the record (review of #1191).
THREAD = "th-1"


async def _record(org: str, rid: str, who: str, text: str, thread: str = THREAD) -> bool:
    return await rcard.record_reply_answer(org, rid, who, text, thread)


async def _take(org: str, rid: str, who: str, text: str, thread: str = THREAD) -> bool:
    return await rcard.take_reply_answer(org, rid, who, text, thread, wait_s=0)


# ── A fake Redis, under the real tenant wrapper ──────────────────────────────


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


@pytest.fixture()
def redis(monkeypatch) -> _Redis:
    from acb_common.tenant_redis import TenantRedis

    fake = _Redis()
    monkeypatch.setattr(rcard, "_tenant_redis", lambda: TenantRedis(fake))
    return fake


# ── 1. The recipients: one rule, on the server ───────────────────────────────

from gateway.routes.email.automation import reply_card as rc  # noqa: E402


class _CardDrafts:
    """A stand-in for the draft that each shown card writes to Drafts."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], list[str], str, str]] = []

    async def keep(self, mail: Any, owner: str, to: list[str], cc: list[str],
                   subject: str, body: str, *, prior: Any = None) -> tuple[str, bool]:
        self.calls.append((to, cc, subject, body))
        return "local-draft-1", False


@pytest.fixture(autouse=True)
def card_drafts(monkeypatch) -> _CardDrafts:
    fake = _CardDrafts()
    monkeypatch.setattr(rc, "_keep_card_draft", fake.keep)

    async def no_record(_mail: Any) -> list[str]:
        return []

    monkeypatch.setattr(rc, "_chat_record", no_record)
    return fake


def _derive(reply_all: bool, **kw: Any) -> tuple[list[str], list[str]]:
    base = {"sender": "kim@contoso.test", "reply_to": [], "to": [MEMBER, "lee@contoso.test"],
            "cc": ["ops@contoso.test", "jane@ACME.test"], "own": {MEMBER, "jane@other.test"}}
    base.update(kw)
    return rc.derive_reply_recipients(reply_all=reply_all, **base)


def test_reply_goes_to_the_sender() -> None:
    assert _derive(False) == (["kim@contoso.test"], [])


def test_reply_honours_the_reply_to() -> None:
    assert _derive(False, reply_to=["desk@contoso.test"]) == (["desk@contoso.test"], [])
    to, cc = _derive(True, reply_to=["desk@contoso.test"])
    assert to == ["desk@contoso.test", "lee@contoso.test"] and cc == ["ops@contoso.test"]
    assert "kim@contoso.test" not in to + cc  # the Reply-To replaces the sender


def test_reply_all_adds_to_and_cc_less_the_members_own_addresses() -> None:
    to, cc = _derive(True)
    assert to == ["kim@contoso.test", "lee@contoso.test"]
    assert cc == ["ops@contoso.test"]
    # Each mailbox of the member is left out, whatever its case.
    assert not {a.lower() for a in to + cc} & {MEMBER, "jane@other.test"}


def test_reply_all_lists_each_address_once() -> None:
    to, cc = _derive(True, to=["kim@contoso.test", "Lee@contoso.test"],
                     cc=["lee@contoso.test", "ops@contoso.test"])
    assert to == ["kim@contoso.test", "Lee@contoso.test"] and cc == ["ops@contoso.test"]


def test_a_reply_to_the_members_own_mail_goes_to_its_to() -> None:
    assert _derive(False, sender=MEMBER) == (["lee@contoso.test"], [])


def test_a_mail_with_no_one_to_answer_gives_no_recipient() -> None:
    assert _derive(False, sender=MEMBER, to=[MEMBER], cc=[]) == ([], [])


def test_the_stored_recipients_parse() -> None:
    row = types.SimpleNamespace(
        account_id=BOX, provider_message_id="p1", thread_id="t1", subject="Quote",
        from_address=json.dumps({"name": "Kim Lee", "email": "kim@contoso.test"}),
        to_addresses=[{"name": "Jane", "email": MEMBER}, {"email": "lee@contoso.test"}],
        cc_addresses=json.dumps([{"email": "ops@contoso.test"}]),
        received_at=None, snippet="Can you confirm?", body_text="")
    me = types.SimpleNamespace(address=MEMBER, label="Work", self_addresses=frozenset({MEMBER}))
    mail = rc._Mail(row, me, [])
    assert mail.recipients(True) == (["kim@contoso.test", "lee@contoso.test"],
                                     ["ops@contoso.test"])
    assert mail.mailbox == f"Work · {MEMBER}" and mail.sender_name == "Kim Lee"


# ── 2. The answer: strict, and bound to what the member posted ───────────────


def test_a_reply_answer_parses() -> None:
    choice = rcard.parse_reply_answer(_answer("save", reply_all=True, cc=["x@y.test"]))
    assert choice == rcard.ReplyChoice("save", True, "Hi Kim,\n\nYes.",
                                       ("kim@contoso.test",), ("x@y.test",), MAIL)


@pytest.mark.parametrize("bad", [
    "APPROVE",
    "REJECT",
    _answer(bcc=["spy@evil.test"]),
    _answer(attachments=["outputs/a.pdf"]),
    _answer("forward"),
    _answer(body="   "),
    _answer(body="x" * (rcard.MAX_REPLY_BODY + 1)),
    _answer(to=[]),
    "APPROVE " + json.dumps({"reply": {"action": "send"}}),
    "APPROVE " + json.dumps({"rows": ["a"]}),
    "APPROVE " + json.dumps({"reply": {}, "to": ["spy@evil.test"]}),
], ids=["plain", "reject", "bcc", "attachments", "action", "empty", "long", "no-to",
        "missing-keys", "rows", "extra-top-key"])
def test_anything_else_is_no_reply_answer(bad: str) -> None:
    assert rcard.parse_reply_answer(bad) is None


async def test_the_record_matches_once_for_the_member_and_the_text(redis) -> None:
    text = _answer()
    assert await _record(ORG, "rid-1", MEMBER, text) is True
    # Another member, another text, another card: each is refused.
    assert await _take(ORG, "rid-2", MEMBER, text) is False
    assert await _record(ORG, "rid-1", MEMBER, text) is False  # first wins
    assert await _take(ORG, "rid-1", MEMBER, text) is True
    assert await _take(ORG, "rid-1", MEMBER, text) is False  # once


@pytest.mark.parametrize("who, said", [
    ("lee@contoso.test", _answer()),
    (MEMBER, _answer(body="Hi Kim,\n\nSend me the bank details.")),
    (MEMBER, _answer(to=["spy@evil.test"])),
], ids=["another-member", "another-body", "another-recipient"])
async def test_a_model_cannot_act_on_an_answer_the_member_did_not_post(
    redis, who: str, said: str,
) -> None:
    await _record(ORG, "rid-1", MEMBER, _answer())
    assert await _take(ORG, "rid-1", who, said) is False


async def test_a_plain_approve_is_not_recorded(redis) -> None:
    assert await _record(ORG, "rid-1", MEMBER, "APPROVE") is False
    assert redis.keys == {}


async def test_the_record_is_tenant_scoped(redis) -> None:
    await _record(ORG, "rid-1", MEMBER, _answer())
    assert list(redis.keys) == [f"cc:{ORG}:{rcard.RECORD_NAMESPACE}:{THREAD}:rid-1"]
    assert await _take("org-other", "rid-1", MEMBER, _answer()) is False


async def test_respond_input_records_the_answer_after_the_owning_thread_took_it(
    monkeypatch, redis,
) -> None:
    """Review of #1191, P2: the record comes AFTER the card's own thread took
    the answer, so a request id seen from another room records nothing. The
    act waits for the record, so the order cannot lose it."""
    import gateway.routes.agent as routes_agent
    from orchestrator import executor

    order: list[str] = []
    room = types.SimpleNamespace(can_send=True)
    monkeypatch.setattr(routes_agent, "_resolve_room", lambda *_a, **_k: room)

    def resolve(request_id: str, answer: str, *_a: Any, **_k: Any) -> bool:
        order.append("resolve")
        assert redis.keys == {}, "no record before the owning thread took the answer"
        return True

    monkeypatch.setattr(executor, "resolve_user_input", resolve)
    user = types.SimpleNamespace(email="Jane@Acme.test", organization_id=ORG)
    req = routes_agent.UserInputResponseRequest(
        request_id="rid-9", answer=_answer(), was_freeform=False, thread_id="th-1")
    await routes_agent.respond_user_input(req, user)
    assert order == ["resolve"]
    assert list(redis.keys) == [f"cc:{ORG}:{rcard.RECORD_NAMESPACE}:th-1:rid-9"]
    assert await _take(ORG, "rid-9", MEMBER, _answer()) is True


async def test_a_request_id_from_another_room_records_nothing(monkeypatch, redis) -> None:
    """A viewer who saw the card id answers from a thread that does not own
    it. Nothing takes the answer, so nothing is recorded, and the member's own
    Send is not blocked."""
    import gateway.routes.agent as routes_agent
    import orchestrator.stream_relay as relay
    from orchestrator import executor

    room = types.SimpleNamespace(can_send=True)
    monkeypatch.setattr(routes_agent, "_resolve_room", lambda *_a, **_k: room)
    monkeypatch.setattr(executor, "resolve_user_input", lambda *_a, **_k: False)

    async def nowhere(*_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(relay, "dispatch_control", nowhere)

    class _Stop(Exception):
        pass

    async def stop(*_a: Any, **_k: Any) -> Any:
        raise _Stop

    # The late-answer path after an undelivered answer is not this test.
    monkeypatch.setattr(routes_agent, "_late_answer_from_row", stop)
    user = types.SimpleNamespace(email="viewer@acme.test", organization_id=ORG)
    req = routes_agent.UserInputResponseRequest(
        request_id="rid-9", answer=_answer(), was_freeform=False, thread_id="th-viewer")
    try:
        await routes_agent.respond_user_input(req, user)
    except Exception:  # noqa: BLE001 - the 409 or the late-answer path
        pass
    assert redis.keys == {}
    assert await _record(ORG, "rid-9", MEMBER, _answer()) is True  # the member's own


async def test_the_take_waits_for_a_record_that_comes_just_after(redis) -> None:
    import asyncio

    async def late() -> None:
        await asyncio.sleep(0.3)
        await _record(ORG, "rid-late", MEMBER, _answer())

    task = asyncio.create_task(late())
    assert await rcard.take_reply_answer(ORG, "rid-late", MEMBER, _answer(), THREAD,
                                         wait_s=2.0) is True
    await task


async def test_the_record_is_bound_to_its_thread(redis) -> None:
    await _record(ORG, "rid-1", MEMBER, _answer())
    assert await _take(ORG, "rid-1", MEMBER, _answer(), thread="th-other") is False
    assert await _take(ORG, "rid-1", MEMBER, _answer()) is True


# ── 3. The two routes ────────────────────────────────────────────────────────


def _mail(reply_to: list[str] | None = None) -> Any:
    row = types.SimpleNamespace(
        account_id=BOX, provider_message_id="p1", thread_id="t1", subject="Quote",
        from_address={"name": "Kim", "email": "kim@contoso.test"},
        to_addresses=[{"email": MEMBER}, {"email": "lee@contoso.test"}],
        cc_addresses=[{"email": "ops@contoso.test"}], received_at=None,
        snippet="Can you confirm the quote?", body_text="")
    me = types.SimpleNamespace(address=MEMBER, label="Work", self_addresses=frozenset({MEMBER}))
    return rc._Mail(row, me, reply_to or [])


class _Routes:
    def __init__(self, monkeypatch, mail: Any) -> None:
        self.sent: list[Any] = []
        self.saved: list[tuple[Any, ...]] = []
        self.mail = mail

        async def load_mail(message_id: str, owner: str) -> Any:
            return self.mail

        async def send(req: Any, background: Any, user: Any) -> dict[str, Any]:
            self.sent.append(req)
            return {"id": "sent-1", "ok": True}

        async def save(mail: Any, owner: str, to: list[str], cc: list[str],
                       subject: str, body: str) -> str:
            self.saved.append((to, cc, subject, body))
            return "draft-1"

        monkeypatch.setattr(rc, "_load_mail", load_mail)
        monkeypatch.setattr(rc, "send_email", send)
        monkeypatch.setattr(rc, "_save_draft", save)


USER = types.SimpleNamespace(email=MEMBER, organization_id=ORG)


async def _act(text: str, rid: str = "rid-1") -> dict[str, Any]:
    req = rc.ReplyCardActRequest(message_id=MAIL, card_request_id=rid,
                                 card_thread_id=THREAD, answer=text)
    return await rc.reply_card_act(req, types.SimpleNamespace(), USER)


async def test_the_act_sends_the_members_body_to_the_derived_recipients(
    monkeypatch, redis,
) -> None:
    routes = _Routes(monkeypatch, _mail())
    text = _answer(reply_all=True, body="Hi all,\n\nConfirmed.",
                   to=["kim@contoso.test", "lee@contoso.test"], cc=["ops@contoso.test"])
    await _record(ORG, "rid-1", MEMBER, text)
    out = await _act(text)
    assert out["action"] == "send"
    [sent] = routes.sent
    assert sent.body_text == "Hi all,\n\nConfirmed."
    assert sent.to == ["kim@contoso.test", "lee@contoso.test"]
    assert sent.cc == ["ops@contoso.test"] and sent.bcc is None
    assert not sent.attachments and not sent.artifacts
    assert sent.reply_to_message_id == MAIL and sent.subject == "Re: Quote"


async def test_the_act_saves_the_members_body_to_drafts(monkeypatch, redis) -> None:
    routes = _Routes(monkeypatch, _mail(reply_to=["desk@contoso.test"]))
    text = _answer("save", body="Edited.", to=["desk@contoso.test"])
    await _record(ORG, "rid-1", MEMBER, text)
    out = await _act(text)
    assert out["action"] == "save" and routes.sent == []
    assert routes.saved == [(["desk@contoso.test"], [], "Re: Quote", "Edited.")]


async def test_the_act_refuses_an_answer_with_no_record(monkeypatch, redis) -> None:
    from fastapi import HTTPException

    routes = _Routes(monkeypatch, _mail())
    with pytest.raises(HTTPException) as err:
        await _act(_answer())
    assert err.value.status_code == 409
    assert routes.sent == [] and routes.saved == []


async def test_a_model_altered_body_is_refused(monkeypatch, redis) -> None:
    from fastapi import HTTPException

    routes = _Routes(monkeypatch, _mail())
    await _record(ORG, "rid-1", MEMBER, _answer(body="Yes."))
    with pytest.raises(HTTPException) as err:
        await _act(_answer(body="Yes. Our bank account is 0042."))
    assert err.value.status_code == 409
    assert routes.sent == [] and routes.saved == []


async def test_the_act_refuses_when_the_recipients_changed(monkeypatch, redis) -> None:
    """The card showed the sender, and the Reply-To now names another
    address: the derived list differs, so nothing goes out."""
    from fastapi import HTTPException

    routes = _Routes(monkeypatch, _mail(reply_to=["desk@contoso.test"]))
    text = _answer(to=["kim@contoso.test"])
    await _record(ORG, "rid-1", MEMBER, text)
    with pytest.raises(HTTPException) as err:
        await _act(text)
    assert err.value.status_code == 409
    assert routes.sent == [] and routes.saved == []


@pytest.mark.parametrize("field", ["to", "cc", "bcc", "body", "body_text", "attachments",
                                   "artifacts"])
def test_the_act_request_takes_no_recipient_body_or_file(field: str) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        rc.ReplyCardActRequest(message_id=MAIL, card_request_id="r", card_thread_id="t",
                               answer="x", **{field: ["spy@evil.test"]})
    if field == "cc":
        # Item 2 (owner, 2026-10-24): the CARD request takes ``cc``. The server
        # classes and records each one, and the act takes a subset of the
        # record only (``test_recipient_intent.py``). The ACT request still
        # takes no cc, above.
        return
    with pytest.raises(ValidationError):
        rc.ReplyCardRequest(message_id=MAIL, **{field: ["spy@evil.test"]})


async def test_the_card_route_drafts_with_the_one_drafter_and_strips_placeholders(
    monkeypatch, card_drafts,
) -> None:
    calls: list[Any] = []

    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    async def drafter(req: Any, user: Any) -> dict[str, Any]:
        calls.append(req)
        return {"draft": "Hi Kim,\n\nYes.\n\n[Your Name]", "created": False}

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "draft_reply_smart", drafter)
    out = await rc.reply_card(rc.ReplyCardRequest(message_id=MAIL), USER)
    [req] = calls
    assert req.create_draft is False and req.message_id == MAIL and req.account_id == BOX
    assert out["body"] == "Hi Kim,\n\nYes."
    # The text of the card is the one draft in Drafts (owner, 2026-10-22).
    # Others are on the mail, so the card opens on Reply all (§22.6).
    assert card_drafts.calls == [(["kim@contoso.test", "lee@contoso.test"],
                                  ["ops@contoso.test"], "Re: Quote", "Hi Kim,\n\nYes.")]
    assert out["reply_all"] is True
    assert out["draft_id"] == "local-draft-1"
    assert out["recipients"] == {
        "reply": {"to": ["kim@contoso.test"], "cc": []},
        "reply_all": {"to": ["kim@contoso.test", "lee@contoso.test"],
                      "cc": ["ops@contoso.test"]},
    }
    assert out["original"]["from"] == "Kim <kim@contoso.test>"
    assert out["original"]["excerpt"] == "Can you confirm the quote?"


async def test_the_card_route_writes_the_reply_all_draft(monkeypatch, card_drafts) -> None:
    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    await rc.reply_card(rc.ReplyCardRequest(message_id=MAIL, draft="Hi all.",
                                            reply_all=True), USER)
    assert card_drafts.calls == [(["kim@contoso.test", "lee@contoso.test"],
                                  ["ops@contoso.test"], "Re: Quote", "Hi all.")]


async def test_a_card_with_no_text_writes_no_draft(monkeypatch, card_drafts) -> None:
    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    async def drafter(req: Any, user: Any) -> dict[str, Any]:
        return {"draft": "", "created": False, "skipped": "low_confidence"}

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "draft_reply_smart", drafter)
    out = await rc.reply_card(rc.ReplyCardRequest(message_id=MAIL), USER)
    assert card_drafts.calls == [] and out["draft_id"] == ""


# ── 4. The tool: pinned parameters, the held run, and no card ────────────────


def test_the_parameter_fence_pins_compose_reply() -> None:
    params = list(inspect.signature(agents.compose_reply).parameters)
    # Item 2 (owner, 2026-10-24): ``cc`` joins the pin. The gateway classes
    # and records each added person (``test_recipient_intent.py``).
    assert params == ["email_id", "account_id", "reply_all", "draft", "instruction", "cc"]
    assert frozenset(params) == eg.CARD_BOUND_SEND_TOOLS["compose_reply"]
    assert agents.compose_reply.__tool_risk__ == {
        "read_only": False, "destructive": True, "idempotent": False,
        "open_world": True, eg.CARD_BOUND_KEY: True}


def _variant(extra: str) -> Any:
    """``compose_reply`` with one more parameter, as a careless change adds."""
    ns: dict[str, Any] = {}
    exec(  # noqa: S102 - a fixed test source
        "async def compose_reply(email_id, account_id, reply_all=False, draft='', "
        f"instruction='', cc=None, {extra}=None):\n"
        "    return ''\n", ns)
    fn = ns["compose_reply"]
    fn.__tool_risk__ = dict(agents.compose_reply.__tool_risk__)
    return fn


@pytest.mark.parametrize("extra", ["to", "cc_list", "bcc", "recipients", "attachments", "body",
                                   "forward_to"])
def test_the_parameter_fence_fails_when_a_parameter_is_added(extra: str) -> None:
    assert eg.is_card_bound_send(agents.compose_reply) is True
    assert eg.is_egress_tool(agents.compose_reply) is False
    variant = _variant(extra)
    assert eg.is_card_bound_send(variant) is False
    assert eg.is_egress_tool(variant) is True


def test_the_mark_alone_keeps_nothing() -> None:
    """A send with the mark and another name is an egress tool."""
    async def send_reply(email_id: str, account_id: str, reply_all: bool = False) -> str:
        return ""

    send_reply.__tool_risk__ = dict(agents.compose_reply.__tool_risk__)  # type: ignore[attr-defined]
    assert eg.is_egress_tool(send_reply) is True
    unmarked = dict(agents.compose_reply.__tool_risk__)
    unmarked.pop(eg.CARD_BOUND_KEY)
    fn = _variant("x")
    fn.__tool_risk__ = unmarked
    assert eg.is_egress_tool(fn) is True


def test_a_held_run_sends_only_through_a_card() -> None:
    """H-374 (owner, "Drafts, with a card" and "Schedule a send",
    2026-10-22): a held run keeps the reply card, send_draft and
    schedule_send. A forward, a new mail and an unsubscribe stay held,
    because the model names their recipients or their link."""
    tools = {fn.__name__: fn for fn in agents._agent_tools()}
    for name in ("compose_reply", "send_draft", "schedule_send"):
        assert eg.is_egress_tool(tools[name]) is False, name
    for name in ("send_email", "forward_email", "unsubscribe_sender"):
        assert eg.is_egress_tool(tools[name]) is True, name
    assert set(eg.CARD_BOUND_SEND_TOOLS) == {"compose_reply", "send_draft", "schedule_send"}


# ── 4b. send_draft (H-374): pinned to the mailbox and the draft id ───────────


def test_the_parameter_fence_pins_send_draft() -> None:
    params = list(inspect.signature(agents.send_draft).parameters)
    assert params == ["account_id", "draft_id"]
    assert frozenset(params) == eg.CARD_BOUND_SEND_TOOLS["send_draft"]
    assert agents.send_draft.__tool_risk__ == {
        "read_only": False, "destructive": True, "idempotent": False,
        "open_world": True, eg.CARD_BOUND_KEY: True}
    assert eg.is_card_bound_send(agents.send_draft) is True
    assert eg.is_card_bound_send("send_draft") is True


def _send_draft_variant(extra: str) -> Any:
    """``send_draft`` with one more parameter, as a careless change adds."""
    ns: dict[str, Any] = {}
    exec(  # a fixed test source
        f"async def send_draft(account_id, draft_id, {extra}=None):\n"
        "    return ''\n", ns)
    fn = ns["send_draft"]
    fn.__tool_risk__ = dict(agents.send_draft.__tool_risk__)
    return fn


@pytest.mark.parametrize("extra", ["to", "cc", "bcc", "recipients", "attachments", "body",
                                   "forward_to", "send_at"])
def test_an_extra_parameter_on_send_draft_breaks_the_pin(extra: str) -> None:
    variant = _send_draft_variant(extra)
    assert eg.is_card_bound_send(variant) is False
    assert eg.is_egress_tool(variant) is True


def test_send_draft_with_no_mark_is_an_egress_tool() -> None:
    async def send_draft(account_id: str, draft_id: str) -> str:
        return ""

    hints = dict(agents.send_draft.__tool_risk__)
    hints.pop(eg.CARD_BOUND_KEY)
    send_draft.__tool_risk__ = hints  # type: ignore[attr-defined]
    assert eg.is_egress_tool(send_draft) is True


def test_the_parameter_fence_pins_schedule_send() -> None:
    params = list(inspect.signature(agents.schedule_send).parameters)
    assert params == ["account_id", "draft_id", "send_at"]
    assert frozenset(params) == eg.CARD_BOUND_SEND_TOOLS["schedule_send"]
    assert agents.schedule_send.__tool_risk__[eg.CARD_BOUND_KEY] is True
    assert eg.is_card_bound_send(agents.schedule_send) is True


@pytest.mark.parametrize("extra", ["to", "cc", "bcc", "attachments", "body"])
def test_an_extra_parameter_on_schedule_send_breaks_the_pin(extra: str) -> None:
    ns: dict[str, Any] = {}
    exec(  # a fixed test source
        f"async def schedule_send(account_id, draft_id, send_at, {extra}=None):\n"
        "    return ''\n", ns)
    fn = ns["schedule_send"]
    fn.__tool_risk__ = dict(agents.schedule_send.__tool_risk__)
    assert eg.is_card_bound_send(fn) is False
    assert eg.is_egress_tool(fn) is True


def test_the_other_sends_join_no_pin_with_the_switch_off(monkeypatch) -> None:
    """§21 (``email_app_master_plan.md``): a send of the held card may carry
    the mark, and the mark alone keeps nothing. While EMAIL_HELD_CARD_SEND is
    off, these sends are on no pin, so a held run holds none of them."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "email_held_card_send", False)
    for name in ("send_email", "forward_email", "unsubscribe_sender"):
        fn = getattr(agents, name)
        assert name not in eg.CARD_BOUND_SEND_TOOLS, name
        assert eg.is_card_bound_send(fn) is False, name
        assert eg.is_egress_tool(fn) is True, name


class _Gateway:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.card_extra: dict[str, Any] = {}
        self.act: dict[str, Any] = {"ok": True, "action": "send", "to": ["kim@contoso.test"],
                                    "cc": [], "mailbox": f"Work · {MEMBER}"}

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        if path == "/email/reply-card":
            return {**self.card_extra, **{
                "account_id": BOX, "mailbox": f"Work · {MEMBER}", "subject": "Re: Quote",
                "original": {"from": "Kim <kim@contoso.test>", "subject": "Quote",
                             "received": "", "excerpt": "Can you confirm?"},
                "recipients": {"reply": {"to": ["kim@contoso.test"], "cc": []},
                               "reply_all": {"to": ["kim@contoso.test", "lee@contoso.test"],
                                             "cc": []}},
                "body": "Hi Kim,\n\nYes.",
                "message_id": MAIL, "notices": [],
            }}
        if path == "/email/reply-card/act":
            return self.act
        raise AssertionError(f"unexpected POST {path}")


@pytest.fixture()
def gw(monkeypatch) -> _Gateway:
    g = _Gateway()
    monkeypatch.setattr(agents, "_post", g.post)
    monkeypatch.setattr(agents, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    return g


def _card_answer(monkeypatch, make: Any) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def card(**kw: Any) -> Any:
        seen.append(kw)
        return make(kw)

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    return seen


def _member_send(kw: dict[str, Any], **choice: Any) -> Any:
    from acb_skills.ask_tools import ReplyAnswer

    text = _answer(**choice)
    return ReplyAnswer("rid-7", text, rcard.parse_reply_answer(text), MEMBER, "th-7")


async def test_compose_reply_sends_the_members_answer_and_nothing_of_its_own(
    monkeypatch, gw,
) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: False)  # a chat that read no outside mail
    cards = _card_answer(monkeypatch, lambda kw: _member_send(kw, body="Edited by Jane."))
    out = await agents.compose_reply(MAIL, BOX)
    assert out == f"Sent to kim@contoso.test, from Work · {MEMBER}."
    [card] = cards
    form = card["reply"]
    assert form["recipients"]["reply"]["to"] == ["kim@contoso.test"]
    assert form["body"] == "Hi Kim,\n\nYes." and form["reply_all"] is False
    # Review of #1191, P2: the title names who the reply goes TO.
    assert card["title"] == "Reply to kim@contoso.test?"
    assert form["message_id"] == MAIL
    [prep, act] = gw.posts
    assert prep == ("/email/reply-card", {"message_id": MAIL})
    # The act carries the member's answer and NO recipient or body of its own.
    assert act == ("/email/reply-card/act", {
        "message_id": MAIL, "card_request_id": "rid-7", "card_thread_id": "th-7",
        "answer": _answer(body="Edited by Jane.")})


async def test_compose_reply_passes_the_agreed_text_to_the_card(monkeypatch, gw) -> None:
    """The chat agreed a text. The card route gets it, and no drafter runs."""
    monkeypatch.setattr(agents, "_egress_held", lambda: False)  # a chat that read no outside mail
    _card_answer(monkeypatch, lambda kw: _member_send(kw, body="x"))
    agreed = "Dear Dr Ravikanth,\n\nThree BQs, please."
    await agents.compose_reply(MAIL, BOX, draft=agreed)
    assert gw.posts[0] == ("/email/reply-card", {"message_id": MAIL, "draft": agreed})
    # The act still carries no body of its own.
    assert "draft" not in gw.posts[1][1]


async def test_the_card_route_shows_the_agreed_text_and_skips_the_drafter(
    monkeypatch,
) -> None:
    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    async def drafter(req: Any, user: Any) -> dict[str, Any]:
        raise AssertionError("the drafter must not run when the chat agreed a text")

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "draft_reply_smart", drafter)
    out = await rc.reply_card(
        rc.ReplyCardRequest(message_id=MAIL, draft="Hi Kim,\n\nAgreed.\n\n[Your Name]"),
        USER)
    assert out["body"] == "Hi Kim,\n\nAgreed."


async def test_compose_reply_saves_and_says_so(monkeypatch, gw) -> None:
    _card_answer(monkeypatch, lambda kw: _member_send(kw, action="save"))
    gw.act = {**gw.act, "action": "save", "cc": ["ops@contoso.test"]}
    out = await agents.compose_reply(MAIL, BOX, reply_all=True)
    assert out == (f"Saved in Drafts of Work · {MEMBER}, to kim@contoso.test, with a copy "
                   "to ops@contoso.test. Nothing was sent.")


async def test_compose_reply_never_trashes_on_a_discard_or_a_timeout(
    monkeypatch, gw,
) -> None:
    """``request_confirmation`` answers None for Discard and for a timeout
    alike. So the tool trashes nothing, and a card that timed out keeps its
    draft. The member's own Discard click posts the trash (``ReplyCardForm``)."""
    monkeypatch.setattr(agents, "_egress_held", lambda: False)  # a chat that read no outside mail
    _card_answer(monkeypatch, lambda kw: None)
    out = await agents.compose_reply(MAIL, BOX)
    assert out.startswith("Reply discarded") and "Nothing was sent." in out
    assert gw.posts == [("/email/reply-card", {"message_id": MAIL})]


async def test_the_form_carries_the_cards_draft_id(monkeypatch, gw) -> None:
    gw.card_extra = {"draft_id": "d-1"}
    cards = _card_answer(monkeypatch, lambda kw: None)
    await agents.compose_reply(MAIL, BOX)
    assert cards[0]["reply"]["draft_id"] == "d-1"


async def test_the_answer_says_when_the_members_draft_stays(monkeypatch, gw) -> None:
    gw.card_extra = {"kept_draft": True}
    _card_answer(monkeypatch, lambda kw: _member_send(kw, action="save"))
    gw.act = {**gw.act, "action": "save"}
    out = await agents.compose_reply(MAIL, BOX)
    assert out.endswith(agents._KEPT_DRAFT) and "earlier draft" in out


async def test_compose_reply_asks_for_the_reply_all_draft(monkeypatch, gw) -> None:
    """The draft in Drafts takes the recipients that the card opens with."""
    _card_answer(monkeypatch, lambda kw: _member_send(kw, body="x"))
    await agents.compose_reply(MAIL, BOX, reply_all=True)
    assert gw.posts[0] == ("/email/reply-card", {"message_id": MAIL, "reply_all": True})


async def test_another_members_answer_does_nothing(monkeypatch, gw) -> None:
    from acb_skills.ask_tools import ReplyAnswer

    text = _answer()
    _card_answer(monkeypatch, lambda kw: ReplyAnswer("r", text, rcard.parse_reply_answer(text),
                                                     "lee@contoso.test"))
    out = await agents.compose_reply(MAIL, BOX)
    assert out.startswith("Nothing was sent.")
    assert [p for p, _ in gw.posts] == ["/email/reply-card"]


async def test_no_card_channel_drafts_saves_and_sends_nothing(monkeypatch, gw) -> None:
    from acb_skills import ask_tools

    monkeypatch.undo()
    monkeypatch.setattr(agents, "_post", gw.post)
    with ask_tools.refuse_cards():
        out = await agents.compose_reply(MAIL, BOX)
    assert out == agents._NO_CARD_CHANNEL
    out = await agents.compose_reply(MAIL, BOX)  # a batch run: no queue, no relay
    assert out == agents._NO_CARD_CHANNEL
    assert gw.posts == []


async def test_a_member_who_writes_over_the_card_gets_a_revise(monkeypatch, gw) -> None:
    """The member asked for a change while the card waited (``ask_tools.REVISED``).
    The tool sends nothing, and it tells the model to show a new card."""
    from acb_skills.ask_tools import REVISED

    _card_answer(monkeypatch, lambda kw: REVISED)
    out = await agents.compose_reply(MAIL, BOX)
    assert out == agents._CARD_REVISED
    assert [p for p, _ in gw.posts] == ["/email/reply-card"]


async def test_no_card_on_whatsapp(monkeypatch, gw) -> None:
    import acb_skills.whatsapp_ui as wa

    monkeypatch.setattr(wa, "current_run", lambda: types.SimpleNamespace(writes=True))
    assert await agents.compose_reply(MAIL, BOX) == agents._NO_CARD_CHANNEL
    assert gw.posts == []


# ── 5. The card: the one confirmation card, with a reply form ────────────────


class _Queue:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def put(self, event: dict[str, Any]) -> None:
        self.events.append(event)


FORM = {
    "message_id": MAIL, "notices": [],
    "mailbox": f"Work · {MEMBER}", "subject": "Re: Quote",
    "original": {"from": "Kim <kim@contoso.test>", "subject": "Quote", "excerpt": "Ok?"},
    "recipients": {"reply": {"to": ["kim@contoso.test"], "cc": []},
                   "reply_all": {"to": ["kim@contoso.test"], "cc": ["ops@contoso.test"]}},
    "body": "Hi Kim,\n\nYes.",
}


async def _ask(monkeypatch, answer: dict[str, Any] | None) -> tuple[Any, _Queue]:
    import asyncio

    from acb_skills import ask_tools
    from orchestrator import executor

    queue = _Queue()
    token = executor._active_run_queue.set(queue)

    async def wait(fut: Any, _timeout: float) -> dict[str, Any]:
        if answer is None:
            raise TimeoutError
        return answer

    monkeypatch.setattr(executor, "wait_user_future", wait)
    try:
        result = await asyncio.wait_for(
            ask_tools.request_confirmation("Reply to Kim?", "From Work", reply=FORM), 5)
    finally:
        executor._active_run_queue.reset(token)
    return result, queue


async def test_the_reply_card_carries_the_form_and_returns_the_answer(monkeypatch) -> None:
    from acb_skills.ask_tools import ReplyAnswer

    text = _answer(body="Edited.")
    result, queue = await _ask(monkeypatch, {"answer": text, "responder": MEMBER})
    asked, closed = queue.events
    assert asked["name"] == "confirmation_requested"
    assert asked["value"]["reply"]["recipients"] == FORM["recipients"]
    assert asked["value"]["reply"]["body"] == FORM["body"]
    assert isinstance(result, ReplyAnswer)
    assert result.answer == text and result.choice.body == "Edited."
    assert result.request_id == asked["value"]["request_id"]
    # The closing event names the verdict only, never the body.
    assert closed["value"] == {"request_id": result.request_id, "answer": "APPROVE"}


@pytest.mark.parametrize("answer", [{"answer": "APPROVE"}, {"answer": "REJECT"}, None,
                                    {"answer": _answer(bcc=["spy@evil.test"])}],
                         ids=["plain-approve", "discard", "timeout", "extra-key"])
async def test_anything_but_a_reply_answer_is_none(monkeypatch, answer: Any) -> None:
    result, _ = await _ask(monkeypatch, answer)
    assert result is None


async def test_a_reply_card_never_auto_approves_with_no_channel() -> None:
    from acb_skills import ask_tools

    out = await ask_tools.request_confirmation(
        "Reply?", reply=FORM, non_interactive_default="approve")
    assert out is None
    with ask_tools.refuse_cards():
        assert await ask_tools.request_confirmation("Reply?", reply=FORM) is None


# ── 6. R8: the real SQL of the two routes, under FORCE RLS ───────────────────
#
# The harness of ``test_email_reply_draft_save.py``: the phase-4-promoted
# two-org catalog, the role ``acb_app_h3rls`` (NOSUPERUSER, NOBYPASSRLS), and
# a fake provider only. CI with no ladder database skips this class.

from contextlib import asynccontextmanager  # noqa: E402

from tests.unit.test_email_reply_draft_save import (  # noqa: E402
    _account,
    _drafts,
    _dsn,
    _inbox_mail,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    app_engine,
    promoted,
)


class _ReplyProvider:
    def __init__(self) -> None:
        self.drafts: list[dict[str, Any]] = []

    async def get_reply_to(self, _pmid: str) -> list[str]:
        return []

    async def create_draft(self, **kwargs: Any) -> str:
        self.drafts.append(kwargs)
        return f"AAMk-reply-{len(self.drafts)}"


@pytest.fixture()
def reply_box(promoted, app_engine, monkeypatch):  # noqa: F811
    import uuid as _uuid

    from acb_auth.roles import UserContext, UserRole
    from sqlalchemy import text as _text

    from tests.unit.test_email_scheduler_tenancy import _assert_non_priv, _purge

    _assert_non_priv(app_engine)
    p = promoted
    owner = f"rcr-{_uuid.uuid4().hex[:8]}@reply-card.test"
    other = f"rcx-{_uuid.uuid4().hex[:8]}@reply-card.test"
    aid = _account(p.admin_engine, org=p.org_b, owner=owner)
    provider = _ReplyProvider()

    @asynccontextmanager
    async def _session(*_a: Any, **_kw: Any):
        yield types.SimpleNamespace(provider=provider, account_id=aid, authed=True,
                                    provider_message_id="AAMk-in-r", owner_email=owner)

    monkeypatch.setattr(rc, "provider_session", _session)
    box = types.SimpleNamespace(
        p=p, aid=aid, owner=owner, provider=provider,
        me=UserContext(email=owner, role=UserRole.EMPLOYEE, organization_id=p.org_b),
        other=UserContext(email=other, role=UserRole.EMPLOYEE, organization_id=p.org_b))
    try:
        yield box
    finally:
        with p.admin_engine.begin() as c:
            c.execute(_text("DELETE FROM email_messages WHERE account_id = "
                            "CAST(:a AS uuid)"), {"a": aid})
        _purge(p.admin_engine, [aid])


async def _in_tenant(box: Any, coro: Any) -> Any:
    from acb_common.db import bind_tenant, release_tenant

    from tests.unit._tenant_ladder import tenant_engine_scope

    token = bind_tenant(box.p.org_b)
    try:
        async with tenant_engine_scope(_dsn(box.p)):
            return await coro()
    finally:
        release_tenant(token)


@_DB_GATE
class TestReplyCardRealSql:
    async def test_the_mail_loads_for_its_member_and_404s_for_another(self, reply_box) -> None:
        from fastapi import HTTPException

        box = reply_box
        mail_id = _inbox_mail(box.p.admin_engine, org=box.p.org_b, account_id=box.aid,
                              pmid="AAMk-in-r")
        mail = await _in_tenant(box, lambda: rc._load_mail(mail_id, box.owner))
        assert mail.account_id == box.aid and mail.subject == "Quote"
        assert mail.recipients(False)[0] == ["ravi@contoso-reply-save.test"]
        with pytest.raises(HTTPException) as err:
            await _in_tenant(box, lambda: rc._load_mail(mail_id, box.other.email))
        assert err.value.status_code == 404
        with pytest.raises(HTTPException):
            await _in_tenant(box, lambda: rc._load_mail("AAMk-in-r", box.owner))

    async def test_save_to_drafts_writes_the_members_body_and_mirrors_it(
            self, reply_box, redis) -> None:
        box = reply_box
        mail_id = _inbox_mail(box.p.admin_engine, org=box.p.org_b, account_id=box.aid,
                              pmid="AAMk-in-r")
        text = _answer("save", body="Edited on the card.",
                       to=["ravi@contoso-reply-save.test"], message_id=mail_id)
        await _record(box.p.org_b, "rid-r8", box.owner, text)
        req = rc.ReplyCardActRequest(message_id=mail_id, card_request_id="rid-r8",
                                     card_thread_id=THREAD, answer=text)
        out = await _in_tenant(box, lambda: rc.reply_card_act(
            req, types.SimpleNamespace(), box.me))
        assert out["action"] == "save"
        [draft] = box.provider.drafts
        assert draft["to"] == ["ravi@contoso-reply-save.test"]
        assert draft["body_text"] == "Edited on the card." and draft["exact_to"] is True
        assert list(_drafts(box.p.admin_engine, box.aid).values()) == ["AAMk-reply-1"]


# ── 7. The client and the server read one answer shape ──────────────────────


def test_the_client_builds_the_answer_the_server_reads() -> None:
    """``lib/replyCard.ts`` writes the answer. Its prefix, its limits and its
    keys must be the server's (``acb_skills.reply_card``), or a Send from the
    card is no reply answer and nothing goes out."""
    import re

    src = (REPO / "workbench/control_plane/src/lib/replyCard.ts").read_text(encoding="utf-8")
    assert f'REPLY_ANSWER_PREFIX = "{rcard.REPLY_ANSWER_PREFIX}"' in src
    assert "MAX_REPLY_BODY = 20_000" in src and rcard.MAX_REPLY_BODY == 20_000
    assert f"MAX_REPLY_RECIPIENTS = {rcard.MAX_REPLY_RECIPIENTS}" in src
    block = re.search(r"const reply = \{(.*?)\};", src, re.S)
    assert block, "replyAnswer builds its object as `const reply = {...}`"
    keys = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
    assert keys == set(rcard.REPLY_ANSWER_KEYS), keys


# ── 8. Security review of #1191 ──────────────────────────────────────────────

#: A body with each kind of hidden text the review named: Unicode tag
#: characters, a zero-width space, a bidi override, and data after 40 blank
#: lines. Built with chr(), so no hidden character sits in this source file.
TAGS = "".join(chr(0xE0000 + ord(c)) for c in "acct 0042")
HIDDEN = ("Hi Kim," + chr(0x200B) + "\n\nYes." + TAGS + "\n" + chr(0x202E) + "evil"
          + "\n" * 40 + "secret 0042   \n")


def _is_hidden(ch: str) -> bool:
    import unicodedata

    return unicodedata.category(ch).startswith("C") and ch not in "\n\t"


def test_the_one_cleaning_leaves_only_visible_text() -> None:
    out = rcard.clean_reply_body(HIDDEN)
    assert not [c for c in out if _is_hidden(c)], out
    assert "\n\n\n" not in out and out == "Hi Kim,\n\nYes.\nevil\n\nsecret 0042"
    # A typed emoji keeps its joiner, and a lone joiner goes.
    family = chr(0x1F468) + chr(0x200D) + chr(0x1F469)
    assert rcard.clean_reply_body(f"Thanks {family}") == f"Thanks {family}"
    assert rcard.clean_reply_body("a" + chr(0x200D) + "b") == "ab"


async def test_the_card_route_cleans_hidden_text_from_the_draft(monkeypatch) -> None:
    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    async def drafter(req: Any, user: Any) -> dict[str, Any]:
        return {"draft": HIDDEN, "created": False}

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "draft_reply_smart", drafter)
    out = await rc.reply_card(rc.ReplyCardRequest(message_id=MAIL), USER)
    assert out["body"] == "Hi Kim,\n\nYes.\nevil\n\nsecret 0042"
    assert out["message_id"] == MAIL


@pytest.mark.parametrize("hidden", [TAGS, chr(0x202E), chr(0x2066)], ids=["tags", "rlo", "lri"])
async def test_the_act_refuses_a_body_with_tag_or_bidi_characters(
    monkeypatch, redis, hidden: str,
) -> None:
    from fastapi import HTTPException

    routes = _Routes(monkeypatch, _mail())
    text = _answer(body="Yes." + hidden)
    await _record(ORG, "rid-1", MEMBER, text)
    with pytest.raises(HTTPException) as err:
        await _act(text)
    assert err.value.status_code == 422
    assert routes.sent == [] and routes.saved == []


async def test_the_act_sends_the_body_cleaned_as_the_card_showed_it(monkeypatch, redis) -> None:
    routes = _Routes(monkeypatch, _mail())
    text = _answer(body="Yes." + chr(0x200B) + "\n" * 40 + "Jane  ")
    await _record(ORG, "rid-1", MEMBER, text)
    await _act(text)
    [sent] = routes.sent
    assert sent.body_text == "Yes.\n\nJane"


async def test_the_answer_acts_only_on_its_own_email(monkeypatch, redis) -> None:
    from fastapi import HTTPException

    routes = _Routes(monkeypatch, _mail())
    text = _answer(message_id="0b0b0b0b-0000-4000-8000-0000000000ff")
    await _record(ORG, "rid-1", MEMBER, text)
    with pytest.raises(HTTPException) as err:
        await _act(text)
    assert err.value.status_code == 409 and routes.sent == []


def test_the_card_says_where_the_reply_goes_and_what_the_send_adds() -> None:
    mail = _mail(reply_to=["desk@other.test"])
    mail.signature, mail.provider = "Jane, Acme", "microsoft"
    notices = mail.notices()
    assert notices[0] == ("Reply goes to desk@other.test, not to the sender "
                          "kim@contoso.test.")
    assert any("signature" in n for n in notices)
    assert any("Outlook adds the email you answer" in n for n in notices)
    assert _mail().notices() == []


def test_the_title_names_the_derived_to_in_full() -> None:
    form = {"recipients": {"reply": {"to": ["desk@other.test"], "cc": []},
                           "reply_all": {"to": ["desk@other.test", "lee@contoso.test"]}}}
    assert agents._reply_title(form, False) == "Reply to desk@other.test?"
    assert agents._reply_title(form, True) == "Reply to desk@other.test, lee@contoso.test?"
    long = {"recipients": {"reply": {"to": ["x" * 130 + "@long.test"], "cc": []}}}
    assert agents._reply_title(long, False) == "Reply to 1 recipient?"


class _FakeImap:
    """Two folders hold the same UID 42, as IMAP allows."""

    BOXES = {
        "INBOX": {"42": b"Message-ID: <inbox-42@x.test>\r\nReply-To: evil@x.test\r\n\r\n"},
        "Archive": {"42": b"Message-ID: <archive-42@x.test>\r\nReply-To: desk@x.test\r\n\r\n"},
    }

    def __init__(self) -> None:
        self.selected: str | None = None

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[Any]]:
        self.selected = mailbox.strip('"')
        return ("OK", [b"1"]) if self.selected in self.BOXES else ("NO", [])

    def uid(self, _cmd: str, uid: str, _what: str) -> tuple[str, list[Any]]:
        raw = self.BOXES.get(self.selected or "", {}).get(uid)
        return ("OK", [(b"1 (BODY[HEADER] {1}", raw)]) if raw else ("OK", [None])


@pytest.fixture()
def imap(monkeypatch) -> Any:
    from email_ingestion.providers.imap import IMAPProvider

    provider = IMAPProvider.__new__(IMAPProvider)
    fake = _FakeImap()

    async def get_imap() -> Any:
        return fake

    async def folders() -> list[Any]:
        return [types.SimpleNamespace(name="INBOX", provider_folder_id="INBOX"),
                types.SimpleNamespace(name="Archive", provider_folder_id="Archive")]

    monkeypatch.setattr(provider, "_get_imap", get_imap, raising=False)
    monkeypatch.setattr(provider, "list_folders", folders, raising=False)
    return provider


async def test_an_imap_uid_collision_reads_the_right_folder_or_names_none(imap) -> None:
    """Review of #1191, P1: UID 42 lives in INBOX and in Archive. A reply to
    the Archive mail reads Archive, and the Message-ID must match."""
    assert await imap.get_reply_to("42", folder="archive",
                                   message_id="<archive-42@x.test>") == ["desk@x.test"]
    # The INBOX mail of the same UID answers only to its own Message-ID.
    assert await imap.get_reply_to("42", folder="inbox",
                                   message_id="<inbox-42@x.test>") == ["evil@x.test"]
    # Another Message-ID, no folder, or no Message-ID: no Reply-To at all.
    assert await imap.get_reply_to("42", folder="inbox",
                                   message_id="<archive-42@x.test>") == []
    assert await imap.get_reply_to("42", folder=None, message_id="<archive-42@x.test>") == []
    assert await imap.get_reply_to("42", folder="archive", message_id=None) == []
    assert await imap.get_reply_to("42", folder="no-such-folder",
                                   message_id="<archive-42@x.test>") == []


# ── 9. Security re-review of #1191: every invisible character ────────────────

NL = chr(10)


def _smuggle(text: str) -> str:
    """The published "emoji smuggling" method: one variation selector per byte,
    after a letter. Bytes 0-15 take U+FE00-FE0F, bytes 16-255 U+E0100-E01EF."""
    out = ""
    for b in text.encode("utf-8"):
        out += chr(0xFE00 + b) if b < 16 else chr(0xE0100 + b - 16)
    return out


#: The reviewer's probe set. Each one is invisible and carries data.
PROBES = {
    "variation-selectors": "Thanks" + _smuggle("OTP 482913"),
    "cgj": "Thanks" + chr(0x034F) * 6,
    "hangul-fillers": "Thanks" + chr(0x3164) + chr(0x115F) + chr(0x1160) + chr(0xFFA0),
    "khmer-inherent": "Thanks" + chr(0x17B4) + chr(0x17B5),
    "braille-blank": "Thanks" + chr(0x2800) * 8,
    "mongolian-fvs": "Thanks" + chr(0x180B) + chr(0x180E),
    "word-joiner": "Thanks" + chr(0x2060) + chr(0x2063),
    "shorthand": "Thanks" + chr(0x1BCA0) + chr(0x1BCA3),
    "musical": "Thanks" + chr(0x1D173) + chr(0x1D17A),
    "null-notehead": "Thanks" + chr(0x1D159) * 4,
    "soft-hyphen": "Thanks" + chr(0x00AD),
    "vs-after-blank": "Thanks" + chr(0x2800) + chr(0xFE0F),
    "two-vs16": chr(0x2764) + chr(0xFE0F) + chr(0xFE0F),
}

#: Real text that needs combining marks or joiners, and must stay whole.
REAL = {
    "hindi": "नमस्ते, कृपया शुक्रवार तक पुष्टि करें।",
    "tamil": "வணக்கம், வெள்ளிக்கிழமைக்குள் உறுதிப்படுத்தவும்.",
    "thai": "สวัสดีครับ กรุณายืนยันภายในวันศุกร์",
    "arabic": "مرحبا، يرجى التأكيد قبل يوم الجمعة.",
    "arabic-vowelled": "بِسْمِ ٱللَّٰهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ مُحَمَّدٌ",
    "vietnamese": "Xin chào, vui lòng xác nhận trước thứ Sáu.",
    "flags": chr(0x1F1EE) + chr(0x1F1F3) + " " + chr(0x1F1EF) + chr(0x1F1F5),
    "zwj-family": chr(0x1F468) + chr(0x200D) + chr(0x1F469) + chr(0x200D) + chr(0x1F467),
    "heart-vs16": chr(0x2764) + chr(0xFE0F) + " thanks",
    "heart-on-fire": chr(0x2764) + chr(0xFE0F) + chr(0x200D) + chr(0x1F525),
    "skin-tone-zwj": chr(0x1F469) + chr(0x1F3FD) + chr(0x200D) + chr(0x1F4BB),
    "keycap": "1" + chr(0xFE0F) + chr(0x20E3) + " first",
}


def _visible(text: str) -> bool:
    return not any(rcard.is_invisible_char(c) for c in text
                   if c not in (chr(0x200D), chr(0xFE0E), chr(0xFE0F)))


@pytest.mark.parametrize("name", sorted(PROBES))
def test_each_probe_comes_out_clean(name: str) -> None:
    out = rcard.clean_reply_body(PROBES[name])
    assert _visible(out), [hex(ord(c)) for c in out]
    assert out in ("Thanks", chr(0x2764) + chr(0xFE0F)), [hex(ord(c)) for c in out]


@pytest.mark.parametrize("name", sorted(REAL))
def test_real_text_and_real_emoji_stay_unchanged(name: str) -> None:
    assert rcard.clean_reply_body(REAL[name]) == REAL[name]
    assert rcard.reply_body_refusal(REAL[name]) is None


@pytest.mark.parametrize("name", ["variation-selectors", "vs-after-blank", "two-vs16"])
def test_the_act_refuses_a_stray_variation_selector(name: str) -> None:
    assert rcard.reply_body_refusal(PROBES[name]) is not None


def test_a_long_run_of_marks_is_refused_and_real_marks_are_not() -> None:
    assert rcard.reply_body_refusal("a" + chr(0x0301) * 5) is not None
    assert rcard.reply_body_refusal("a" + chr(0x0301) * 2) is None


async def test_the_probe_set_is_clean_at_the_card_route(monkeypatch) -> None:
    body = NL.join(PROBES.values())

    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    async def drafter(req: Any, user: Any) -> dict[str, Any]:
        return {"draft": body, "created": False}

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "draft_reply_smart", drafter)
    out = await rc.reply_card(rc.ReplyCardRequest(message_id=MAIL), USER)
    assert _visible(out["body"])
    assert out["body"] == rcard.clean_reply_body(body)
    assert "OTP" not in out["body"] and chr(0xE0100) not in out["body"]


@pytest.mark.parametrize("name", sorted(PROBES))
async def test_each_probe_is_refused_or_cleaned_at_the_act(
    monkeypatch, redis, name: str,
) -> None:
    from fastapi import HTTPException

    routes = _Routes(monkeypatch, _mail())
    text = _answer(body=PROBES[name])
    await _record(ORG, "rid-1", MEMBER, text)
    try:
        await _act(text)
    except HTTPException as err:
        assert err.status_code == 422
        assert routes.sent == []
        return
    [sent] = routes.sent
    assert _visible(sent.body_text) and sent.body_text in ("Thanks", chr(0x2764) + chr(0xFE0F))


def test_the_one_rule_covers_default_ignorable_code_point() -> None:
    """``INVISIBLE_CODE_POINTS`` holds every Default_Ignorable_Code_Point that
    the ``regex`` package knows, so a new Unicode version cannot leave a hole
    in the hand list. The list also holds the blank filler U+2800."""
    regex = pytest.importorskip("regex")
    pattern = regex.compile(r"\p{Default_Ignorable_Code_Point}")
    missing = [hex(cp) for cp in range(0x110000)
               if pattern.match(chr(cp)) and not rcard.is_invisible_char(chr(cp))]
    assert missing == []
    assert rcard.is_invisible_char(chr(0x2800))


# ── 10. Review of #1191, P2: a space that only looks like a space ─────────────
#
# A body can swap some plain spaces for NBSP, an em space or an ideographic
# space and hide a few bits per word. Each Zs character is a plain space.

#: Every Unicode Zs code point but U+0020 (Unicode 15.1).
LOOKALIKE_SPACES = [0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000]


def test_a_lookalike_space_comes_out_as_a_plain_space() -> None:
    body = "Hi" + chr(0x00A0) + "Kim," + chr(0x2003) + "yes" + chr(0x3000) + "please."
    out = rcard.clean_reply_body(body)
    assert out.encode("utf-8") == b"Hi Kim, yes please."


@pytest.mark.parametrize("cp", LOOKALIKE_SPACES)
def test_each_zs_code_point_is_a_plain_space(cp: int) -> None:
    assert rcard.is_lookalike_space(chr(cp))
    assert rcard.clean_reply_body("a" + chr(cp) + "b") == "a b"
    assert not rcard.is_lookalike_space(" ")


def test_the_null_notehead_leaves() -> None:
    assert rcard.clean_reply_body("Thanks" + chr(0x1D159) + "Kim") == "ThanksKim"
    assert rcard.is_invisible_char(chr(0x1D159))


def test_a_plain_body_is_unchanged() -> None:
    body = "Hi Kim,\n\nYes, see you Friday.\nThanks,\nJane"
    assert rcard.clean_reply_body(body) == body


@pytest.mark.parametrize("name", ["hindi", "tamil", "zwj-family", "heart-on-fire", "keycap"])
def test_real_text_with_lookalike_spaces_keeps_every_other_character(name: str) -> None:
    body = REAL[name].replace(" ", chr(0x00A0)) + chr(0x2009) + "ok"
    out = rcard.clean_reply_body(body)
    assert [c for c in out if c != " "] == [c for c in body if not rcard.is_lookalike_space(c)]
    assert all(c == " " for c in out if unicodedata.category(c) == "Zs")


def test_the_email_agent_card_text_reads_the_same_rule() -> None:
    for probe in PROBES.values():
        shown = agents._card_text(probe, 500)
        assert _visible(shown), [hex(ord(c)) for c in shown]
    assert agents._is_hidden_char(chr(0x3164)) and agents._is_hidden_char(chr(0xE0101))
    src = (REPO / "apps/agents/agent-email-assistant/agents.py").read_text(encoding="utf-8")
    assert "from acb_skills.reply_card import is_invisible_char" in src


# ── A saved reply draft is addressed by the one rule ─────────────────────────
#
# ``/email/draft-reply`` and ``/email/drafts/save`` addressed the draft to the
# sender alone. They now take To from ``reply_card.reply_recipients``, and a
# fault falls back to the sender. Mutation: return ``[fallback]`` always.


async def test_a_saved_draft_takes_the_derived_to(monkeypatch) -> None:
    from gateway.routes.email.automation import drafting

    async def derived(message_id: str, owner: str, *, reply_all: bool = False):
        assert (message_id, owner, reply_all) == (MAIL, MEMBER, False)
        return ["kim@contoso.test"], []

    monkeypatch.setattr(rc, "reply_recipients", derived)
    assert await drafting._reply_to_list(MAIL, MEMBER, "me@fracktal.test") == [
        "kim@contoso.test"]


async def test_a_saved_draft_falls_back_to_the_sender(monkeypatch) -> None:
    from gateway.routes.email.automation import drafting

    async def broken(message_id: str, owner: str, *, reply_all: bool = False):
        raise RuntimeError("provider down")

    monkeypatch.setattr(rc, "reply_recipients", broken)
    assert await drafting._reply_to_list(MAIL, MEMBER, "kim@contoso.test") == [
        "kim@contoso.test"]


def test_a_revised_card_adds_a_cc_in_the_chat():
    """Owner report, 2026-10-22: the member asked three times to CC two
    people, and the sub-agent redrew the same card. A Cc now goes on the
    card's draft with edit_draft, in the chat (R7 fence)."""
    text = agents._CARD_REVISED
    assert "find_contact" in text and "edit_draft" in text
    # Item 2 (owner, 2026-10-24): the Cc goes on ONE new reply card through
    # ``cc``, and the card keeps the Cc of the email. A new card no longer
    # resets the Cc, so the old line about it went.
    assert "compose_reply" in text and "in cc, in ONE call" in text
    assert "Cc of the email" in text and "never drop one" in text
    assert "\"X and Y\" means one mail to both" in text
    assert "Never send the member to the Email app" in text

async def test_a_revised_card_names_its_draft_id(monkeypatch, gw) -> None:
    """The member writes "add Kiran to CC" while the card is open. The answer
    names the card's draft, so edit_draft can add the Cc to it."""
    from acb_skills.ask_tools import REVISED

    _card_answer(monkeypatch, lambda kw: REVISED)
    gw.card_extra = {"draft_id": "d-123"}
    out = await agents.compose_reply(MAIL, BOX)
    assert out.startswith(agents._CARD_REVISED)
    assert "The draft_id of this reply in Drafts is d-123." in out


# ── §22.6: the card opens on Reply all when others are on the mail ───────────


def test_auto_reply_all_needs_someone_besides_the_sender() -> None:
    assert rc.auto_reply_all(["kim@x.test"], [], ["kim@x.test", "lee@x.test"], [])
    assert rc.auto_reply_all(["kim@x.test"], [], ["kim@x.test"], ["ops@x.test"])
    assert not rc.auto_reply_all(["kim@x.test"], [], ["kim@x.test"], [])


async def test_the_card_route_keeps_an_explicit_reply(monkeypatch, card_drafts) -> None:
    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    async def drafter(req: Any, user: Any) -> dict[str, Any]:
        return {"draft": "Hi Kim,\n\nYes.", "created": False}

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "draft_reply_smart", drafter)
    out = await rc.reply_card(rc.ReplyCardRequest(message_id=MAIL, reply_all=False), USER)
    assert out["reply_all"] is False
    assert card_drafts.calls[0][:2] == (["kim@contoso.test"], [])


async def test_compose_reply_leaves_reply_all_to_the_server(monkeypatch, gw) -> None:
    """No ``reply_all`` is sent, and the card opens on the server's choice."""
    monkeypatch.setattr(agents, "_egress_held", lambda: False)  # a chat that read no outside mail
    gw.card_extra = {"reply_all": True}
    cards = _card_answer(monkeypatch, lambda kw: _member_send(kw, body="x"))
    await agents.compose_reply(MAIL, BOX)
    assert gw.posts[0] == ("/email/reply-card", {"message_id": MAIL})
    assert cards[0]["reply"]["reply_all"] is True
    assert cards[0]["title"] == "Reply to kim@contoso.test, lee@contoso.test?"


async def test_compose_reply_false_is_sent_on_as_reply(monkeypatch, gw) -> None:
    _card_answer(monkeypatch, lambda kw: _member_send(kw, body="x"))
    await agents.compose_reply(MAIL, BOX, reply_all=False)
    assert gw.posts[0] == ("/email/reply-card", {"message_id": MAIL, "reply_all": False})


async def test_compose_reply_in_a_held_run_asks_no_server_reply_all(monkeypatch, gw) -> None:
    """A held chat does not let the server choose Reply all. The card may open
    on Reply (§21), and the draft in Drafts must name the same people as the
    card (review of §22.6, P2)."""
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    _card_answer(monkeypatch, lambda kw: _member_send(kw, body="x"))
    await agents.compose_reply(MAIL, BOX)
    assert gw.posts[0] == ("/email/reply-card", {"message_id": MAIL, "reply_all": False})


async def test_compose_reply_outside_a_held_run_lets_the_server_choose(monkeypatch, gw) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    _card_answer(monkeypatch, lambda kw: _member_send(kw, body="x"))
    await agents.compose_reply(MAIL, BOX)
    assert gw.posts[0] == ("/email/reply-card", {"message_id": MAIL})


# ── The tick boxes of the reply card (owner, 2026-10-24) ────────────────────
#
# Each To and Cc of the card has a tick box. The gateway stays the only source
# of the lists, the act takes only a SUBSET of the list of the chosen mode,
# and the mail goes to exactly the ticked people.


async def test_the_act_sends_only_to_the_ticked_people(monkeypatch, redis) -> None:
    """Mutation: send the derived lists in place of the ticked ones → red."""
    routes = _Routes(monkeypatch, _mail())
    text = _answer(reply_all=True, body="Hi Kim.", to=["kim@contoso.test"], cc=[])
    await _record(ORG, "rid-1", MEMBER, text)
    out = await _act(text)
    [sent] = routes.sent
    assert sent.to == ["kim@contoso.test"] and sent.cc is None
    assert out["to"] == ["kim@contoso.test"] and out["cc"] == []


async def test_the_act_keeps_the_server_order_of_the_ticked_people(monkeypatch, redis) -> None:
    routes = _Routes(monkeypatch, _mail())
    text = _answer(reply_all=True, to=["LEE@contoso.test", "kim@contoso.test"],
                   cc=["ops@contoso.test"])
    await _record(ORG, "rid-1", MEMBER, text)
    await _act(text)
    assert routes.sent[0].to == ["kim@contoso.test", "lee@contoso.test"]


async def test_a_save_writes_the_ticked_people_to_the_draft(monkeypatch, redis) -> None:
    """The draft in Drafts holds what the card sends."""
    routes = _Routes(monkeypatch, _mail())
    text = _answer("save", reply_all=True, body="Draft.", to=["lee@contoso.test"],
                   cc=["ops@contoso.test"])
    await _record(ORG, "rid-1", MEMBER, text)
    await _act(text)
    assert routes.saved == [(["lee@contoso.test"], ["ops@contoso.test"], "Re: Quote", "Draft.")]


@pytest.mark.parametrize(("to", "cc"), [
    (["kim@contoso.test", "spy@evil.test"], []),           # an added To
    (["kim@contoso.test"], ["spy@evil.test"]),             # an added Cc
    (["kim@contoso.test", "KIM@contoso.test"], []),        # one person twice
    (["ops@contoso.test"], []),                            # a Cc moved into To
])
async def test_the_act_refuses_a_person_that_the_server_did_not_list(
    monkeypatch, redis, to: list[str], cc: list[str],
) -> None:
    """Mutation: drop ``ticked_subset`` → red. 409, and nothing goes."""
    from fastapi import HTTPException

    routes = _Routes(monkeypatch, _mail())
    text = _answer(reply_all=True, to=to, cc=cc)
    await _record(ORG, "rid-1", MEMBER, text)
    with pytest.raises(HTTPException) as err:
        await _act(text)
    assert err.value.status_code == 409
    assert routes.sent == [] and routes.saved == []


def test_an_answer_with_no_ticked_to_is_no_answer() -> None:
    assert rcard.parse_reply_answer(_answer(to=[])) is None


async def test_the_card_route_starts_each_person_ticked(monkeypatch, card_drafts) -> None:
    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    out = await rc.reply_card(rc.ReplyCardRequest(message_id=MAIL, draft="Hi."), USER)
    assert out["ticked"] == out["recipients"]


async def test_untick_new_leaves_each_new_person_off_the_card_and_the_draft(
    monkeypatch, card_drafts,
) -> None:
    """A held chat with the switch on: the card opens on Reply all (§22.6),
    each new person starts unticked, and the draft holds only the ticked
    people. Mutation: write the draft with the whole list → red."""
    from tests.unit import _held_card_fakes as F

    async def load_mail(message_id: str, owner: str) -> Any:
        return _mail()

    async def classes(owner: str, addresses: list[str], *, primary: list[str], **_kw) -> dict:
        return {"kim@contoso.test": "thread", "lee@contoso.test": "new",
                "ops@contoso.test": "org"}

    monkeypatch.setattr(rc, "_load_mail", load_mail)
    monkeypatch.setattr(rc, "_reply_classes", classes)
    F.flag_on(monkeypatch)
    out = await rc.reply_card(
        rc.ReplyCardRequest(message_id=MAIL, draft="Hi.", untick_new=True), USER)
    assert out["reply_all"] is True
    assert out["ticked"]["reply_all"] == {"to": ["kim@contoso.test"],
                                          "cc": ["ops@contoso.test"]}
    assert card_drafts.calls == [(["kim@contoso.test"], ["ops@contoso.test"],
                                  "Re: Quote", "Hi.")]
    # The switch off: no class, so each person starts ticked, as before.
    F.flag_off(monkeypatch)
    card_drafts.calls.clear()
    out = await rc.reply_card(
        rc.ReplyCardRequest(message_id=MAIL, draft="Hi.", untick_new=True), USER)
    assert out["ticked"] == out["recipients"]
    assert card_drafts.calls[0][0] == ["kim@contoso.test", "lee@contoso.test"]


async def test_compose_reply_hands_the_servers_ticks_to_the_card(monkeypatch, gw) -> None:
    gw.card_extra = {"ticked": {"reply": {"to": ["kim@contoso.test"], "cc": []},
                                "reply_all": {"to": ["kim@contoso.test"], "cc": []}}}
    cards = _card_answer(monkeypatch, lambda kw: None)
    await agents.compose_reply(MAIL, BOX)
    assert cards[0]["reply"]["ticked"]["reply_all"] == {"to": ["kim@contoso.test"], "cc": []}


async def test_an_older_gateway_starts_each_person_ticked(monkeypatch, gw) -> None:
    cards = _card_answer(monkeypatch, lambda kw: None)
    await agents.compose_reply(MAIL, BOX)
    form = cards[0]["reply"]
    assert form["ticked"] == form["recipients"]


async def test_a_held_chat_with_the_switch_on_asks_untick_new_and_lets_the_server_choose(
    monkeypatch, gw,
) -> None:
    """Mutation: keep forcing Reply in a held chat with the switch on → red."""
    from tests.unit import _held_card_fakes as F

    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    monkeypatch.setattr("acb_skills.ask_tools.reply_card_channel_open", lambda: True)
    gw.card_extra = {"reply_all": True,
                     "classes": {"kim@contoso.test": "thread", "lee@contoso.test": "new"},
                     "ticked": {"reply": {"to": ["kim@contoso.test"], "cc": []},
                                "reply_all": {"to": ["kim@contoso.test"], "cc": []}}}
    cards = _card_answer(monkeypatch, lambda kw: None)
    await agents.compose_reply(MAIL, BOX)
    assert gw.posts[0] == ("/email/reply-card", {"message_id": MAIL, "untick_new": True})
    form = cards[0]["reply"]
    assert form["reply_all"] is True
    assert form["ticked"]["reply_all"]["to"] == ["kim@contoso.test"]
    assert agents._REPLY_NEW_UNTICKED_NOTICE in form["notices"]
    _ = F


async def test_a_held_chat_with_the_switch_off_keeps_reply(monkeypatch, gw) -> None:
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr("acb_skills.ask_tools.reply_card_channel_open", lambda: True)
    cards = _card_answer(monkeypatch, lambda kw: None)
    await agents.compose_reply(MAIL, BOX)
    assert gw.posts[0] == ("/email/reply-card", {"message_id": MAIL, "reply_all": False})
    assert cards[0]["reply"]["ticked"] == cards[0]["reply"]["recipients"]


def test_the_card_form_refuses_a_tick_of_a_person_that_is_not_listed() -> None:
    from acb_skills.ask_tools import clean_reply_form

    form = agents._reply_form({
        "message_id": MAIL, "recipients": {
            "reply": {"to": ["kim@contoso.test"], "cc": []},
            "reply_all": {"to": ["kim@contoso.test", "lee@contoso.test"], "cc": []}},
        "body": "Hi"}, True)
    assert clean_reply_form(form)["ticked"]["reply_all"]["to"] == [
        "kim@contoso.test", "lee@contoso.test"]
    form["ticked"]["reply_all"]["to"] = ["spy@evil.test"]
    with pytest.raises(ValueError):
        clean_reply_form(form)


def test_a_card_with_six_notices_shows_and_loses_none() -> None:
    """The card shows four notices (clean_reply_form refuses more); the rest
    go into the note. Mutation: pass every notice to the card → the form is
    refused → no card shows → red (the defect of 2026-10-24)."""
    from acb_skills.ask_tools import clean_reply_form

    notices = [f"notice {i}" for i in range(1, 7)]
    form = agents._reply_form({
        "message_id": MAIL, "recipients": {
            "reply": {"to": ["kim@contoso.test"], "cc": []},
            "reply_all": {"to": ["kim@contoso.test"], "cc": []}},
        "body": "Hi", "notices": notices}, True)
    clean = clean_reply_form(form)
    assert clean["notices"] == notices[:4]
    assert "notice 5" in form["note"] and "notice 6" in form["note"]
