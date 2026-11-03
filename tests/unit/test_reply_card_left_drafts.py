"""A reply card whose reply already left (owner report, 2026-11-03, task 1526).

Spec: ``project-docs/specs/email_app_master_plan.md`` §18.9, the subsection
"A card whose draft left Drafts". The chat reply card writes its reply to
Drafts at once. The member then sent that draft from the Email app while the
card still waited. A Send on the card mailed each person a second time, and a
Save made an orphan draft.

What this file fences (R7). Each rule names the test that fails:

1. The act refuses Send and Save with a 409 ``draft_gone`` when the draft
   that the card wrote is no longer in Drafts.
   ``test_the_act_refuses_*_when_the_draft_left``.
2. The act refuses with a 409 ``already_replied`` when the mailbox sent a
   reply in the thread after the card was made, with a draft or none.
   ``test_the_act_refuses_a_reply_after_the_card*``.
3. The act still sends when the draft is in Drafts and no reply followed,
   and when the reply came before the card. ``test_the_act_still_sends_*``.
4. The draft id and the time come from the server's record of THIS card,
   never from the request. ``test_the_act_takes_no_draft_id_*``,
   ``test_the_card_route_records_*``.
5. A fault of the check sends nothing (503). ``test_a_failed_check_*``.
6. ``compose_reply`` gives the model the fact, never "Sent", and a closed
   card whose draft left never reads "discarded". ``test_compose_reply_*``.

The real SQL is in ``test_reply_card_left_drafts_r8.py``. No test sends a mail.
"""
from __future__ import annotations

import contextlib
import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from acb_skills import reply_card as rcard
from fastapi import HTTPException
from gateway.routes.email.automation import reply_card as rc
from pydantic import ValidationError

from tests.unit import _held_card_fakes as F
from tests.unit._held_card_fakes import email_tool_mode  # noqa: F401 - the two modes

ORG = "org-left-1"
MEMBER = "jane@acme.test"
MAIL = "0b0b0b0b-0000-4000-8000-00000000000b"
DRAFT = "0d0d0d0d-0000-4000-8000-0000000000aa"
BOX = "acc-1"
THREAD = "th-left-1"
USER = types.SimpleNamespace(email=MEMBER, organization_id=ORG)

agents = F.load_email_agent("ea_left_drafts")


def _answer(action: str = "send") -> str:
    reply = {"action": action, "reply_all": True, "body": "Hi Kim,\n\nYes.",
             "to": ["kim@contoso.test", "lee@contoso.test"], "cc": ["ops@contoso.test"],
             "message_id": MAIL}
    return "APPROVE " + json.dumps({"reply": reply})


def _mail() -> Any:
    row = types.SimpleNamespace(
        account_id=BOX, provider_message_id="p1", thread_id="t1", subject="Quote",
        from_address={"name": "Kim", "email": "kim@contoso.test"},
        to_addresses=[{"email": MEMBER}, {"email": "lee@contoso.test"}],
        cc_addresses=[{"email": "ops@contoso.test"}], received_at=None,
        snippet="Can you confirm the quote?", body_text="")
    me = types.SimpleNamespace(address=MEMBER, label="Work", self_addresses=frozenset({MEMBER}))
    return rc._Mail(row, me, [])


class _World:
    """The act's world: the mail, the send, the save, the draft rows and the
    thread's newest Sent mail. ``draft_reads`` names each draft id that the
    guard asked about."""

    def __init__(self, monkeypatch) -> None:
        self.redis = F.install_redis(monkeypatch)
        self.sent: list[Any] = []
        self.saved: list[Any] = []
        self.in_drafts: set[str] = set()
        self.draft_reads: list[str] = []
        self.recent: dict[str, str] = {}
        self.fail = False

        async def load_mail(message_id: str, owner: str) -> Any:
            return _mail()

        async def send(req: Any, background: Any, user: Any) -> dict[str, Any]:
            self.sent.append(req)
            return {"id": "sent-1", "ok": True}

        async def save(mail: Any, owner: str, to: list[str], cc: list[str],
                       subject: str, body: str, **_kw: Any) -> str:
            self.saved.append((to, cc, body))
            return "draft-saved"

        @contextlib.asynccontextmanager
        async def session():
            if self.fail:
                raise RuntimeError("pool exhausted")
            yield types.SimpleNamespace()

        async def in_drafts(db: Any, owner: str, draft_id: str) -> bool:
            assert owner == MEMBER
            self.draft_reads.append(draft_id)
            return draft_id in self.in_drafts

        async def recent(db: Any, account_id: str, thread_id: str | None) -> dict[str, str]:
            return dict(self.recent)

        monkeypatch.setattr(rc, "_load_mail", load_mail)
        monkeypatch.setattr(rc, "send_email", send)
        monkeypatch.setattr(rc, "_save_draft", save)
        monkeypatch.setattr(rc, "_tenant_session", session)
        monkeypatch.setattr(rc, "draft_in_drafts", in_drafts)
        monkeypatch.setattr(rc, "recent_thread_reply", recent)

    async def card(self, draft_id: str = DRAFT) -> str:
        """Make the record of a card, as ``POST /email/reply-card`` does."""
        card_id = await rc._write_reply_record(USER, MAIL, [])
        assert card_id
        await rc._note_card_draft(USER, card_id, MAIL, draft_id)
        return card_id

    async def act(self, card_id: str, action: str = "send") -> dict[str, Any]:
        text = _answer(action)
        assert await rcard.record_reply_answer(ORG, card_id, MEMBER, text, THREAD)
        req = rc.ReplyCardActRequest(message_id=MAIL, card_request_id=card_id,
                                     card_thread_id=THREAD, answer=text)
        return await rc.reply_card_act(req, types.SimpleNamespace(), USER)


def _when(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


# ── 1. The draft left Drafts ────────────────────────────────────────────────


@pytest.mark.parametrize("action", ["send", "save"])
async def test_the_act_refuses_send_and_save_when_the_draft_left(monkeypatch, action) -> None:
    """Mutation: drop the draft check, or put it after the send → red."""
    w = _World(monkeypatch)
    card_id = await w.card()
    with pytest.raises(HTTPException) as err:
        await w.act(card_id, action)
    assert err.value.status_code == 409
    assert err.value.detail["reason"] == rcard.DRAFT_GONE == "draft_gone"
    assert err.value.detail["message"] == rc.REPLY_LEFT_DRAFTS
    assert "Nothing was sent or saved." in rc.REPLY_LEFT_DRAFTS
    assert w.sent == [] and w.saved == []
    assert w.draft_reads == [DRAFT]


# ── 2. A reply after the card ───────────────────────────────────────────────


async def test_the_act_refuses_a_reply_after_the_card(monkeypatch) -> None:
    w = _World(monkeypatch)
    w.in_drafts = {DRAFT}
    card_id = await w.card()
    w.recent = {"message_id": "s-1", "sent_at": _when(5)}
    with pytest.raises(HTTPException) as err:
        await w.act(card_id)
    assert err.value.status_code == 409
    assert err.value.detail == {"reason": rcard.ALREADY_REPLIED,
                                "message": rc.REPLY_ALREADY_SENT}
    assert w.sent == [] and w.saved == []


async def test_the_act_refuses_a_reply_after_the_card_with_no_draft(monkeypatch) -> None:
    """A card that wrote no draft is still guarded by the time of the card."""
    w = _World(monkeypatch)
    card_id = await w.card(draft_id="")
    w.recent = {"message_id": "s-1", "sent_at": _when(5)}
    with pytest.raises(HTTPException) as err:
        await w.act(card_id, "save")
    assert err.value.detail["reason"] == "already_replied"
    assert w.draft_reads == [] and w.saved == []


# ── 3. The act still sends ──────────────────────────────────────────────────


async def test_the_act_still_sends_when_the_draft_is_in_drafts(monkeypatch) -> None:
    w = _World(monkeypatch)
    w.in_drafts = {DRAFT}
    card_id = await w.card()
    out = await w.act(card_id)
    assert out["action"] == "send" and len(w.sent) == 1
    assert w.draft_reads == [DRAFT]


async def test_the_act_still_sends_after_a_reply_that_came_before_the_card(
    monkeypatch,
) -> None:
    """The card shows that earlier reply (item 6). It does not block it."""
    w = _World(monkeypatch)
    w.in_drafts = {DRAFT}
    w.recent = {"message_id": "s-0", "sent_at": _when(-120)}
    card_id = await w.card()
    out = await w.act(card_id, "save")
    assert out["action"] == "save" and w.saved and w.sent == []


async def test_a_record_of_an_older_card_checks_nothing(monkeypatch) -> None:
    """A record with no time and no draft id (made before this change)."""
    w = _World(monkeypatch)
    w.recent = {"message_id": "s-1", "sent_at": _when(5)}
    card = rc._CardRecord([], [])
    assert await rc._reply_left(_mail(), MEMBER, card) is None
    assert w.draft_reads == []


# ── 4. Never from the request ───────────────────────────────────────────────


@pytest.mark.parametrize("field", ["draft_id", "made_at", "card_draft_id"])
def test_the_act_takes_no_draft_id_or_time_from_the_request(field: str) -> None:
    with pytest.raises(ValidationError):
        rc.ReplyCardActRequest(message_id=MAIL, card_request_id="r", card_thread_id="t",
                               answer="x", **{field: DRAFT})


async def test_the_guard_reads_the_draft_id_of_this_cards_record(monkeypatch) -> None:
    """Two cards, two drafts: the act of card B checks draft B only."""
    w = _World(monkeypatch)
    other = "0d0d0d0d-0000-4000-8000-0000000000bb"
    w.in_drafts = {DRAFT}
    await w.card(draft_id=other)
    card_b = await w.card(draft_id=DRAFT)
    await w.act(card_b)
    assert w.draft_reads == [DRAFT] and len(w.sent) == 1


async def test_a_record_of_another_member_names_no_draft(monkeypatch) -> None:
    w = _World(monkeypatch)
    card_id = await w.card()
    stranger = types.SimpleNamespace(email="omar@acme.test", organization_id=ORG)
    card = await rc._take_reply_record(stranger, card_id, MAIL)
    assert card == rc._CardRecord([], [])


async def test_the_card_route_records_the_time_and_the_draft(monkeypatch) -> None:
    """``_note_card_draft`` adds the LOCAL id that the draft write gave, to
    THIS card's record, and keeps the time of the card."""
    w = _World(monkeypatch)
    before = rc.time.time()
    card_id = await w.card()
    card = await rc._take_reply_record(USER, card_id, MAIL)
    assert card.draft_id == DRAFT
    assert card.made_at is not None and before <= card.made_at <= rc.time.time()


class _RouteWorld:
    """The REAL ``POST /email/reply-card`` route, then the act on its card.

    The world of ``test_recipient_intent.py`` (a fake database, a fake
    tenant Redis and a fake mail), with a draft write that answers a UUID,
    and a Drafts folder that no longer holds it (or still holds it)."""

    def __init__(self, monkeypatch, *, in_drafts: bool = False) -> None:
        from tests.unit import test_recipient_intent as ri

        self.ri = ri
        self.w = ri._World(monkeypatch)
        self.draft_reads: list[str] = []

        async def keep(*_a: Any, **_k: Any) -> tuple[str, bool]:
            return DRAFT, False

        async def gone(db: Any, owner: str, draft_id: str) -> bool:
            self.draft_reads.append(draft_id)
            return in_drafts

        monkeypatch.setattr(rc, "_keep_card_draft", keep)
        monkeypatch.setattr(rc, "draft_in_drafts", gone)

    async def card_then_act(self) -> tuple[dict[str, Any], Any]:
        out = await self.w.card()
        start = out["ticked"]["reply_all"]
        try:
            return out, await self.w.act(out, to=start["to"], cc=start["cc"])
        except HTTPException as exc:
            return out, exc


async def test_the_card_route_notes_its_draft_so_the_act_refuses_it(monkeypatch) -> None:
    """Fix round 1, item 1: the route itself writes the draft id to the
    record of its card. Mutation: delete the ``_note_card_draft`` call in
    ``reply_card`` → the act sends → red."""
    r = _RouteWorld(monkeypatch)
    out, got = await r.card_then_act()
    assert out["draft_id"] == DRAFT and out["card_id"]
    assert isinstance(got, HTTPException), got
    assert got.status_code == 409 and got.detail["reason"] == rcard.DRAFT_GONE
    assert r.draft_reads == [DRAFT]
    assert r.w.sent == []


async def test_a_failed_second_write_refuses_the_act(monkeypatch) -> None:
    """Item 10: the record says ``draft_pending``, and the write of the draft
    id fails. The act refuses with ``draft_unconfirmed`` and reads no draft.
    Mutation: drop the pending check of ``_reply_left`` → the act sends → red."""
    r = _RouteWorld(monkeypatch)
    store = r.w.redis
    first_set = store.set

    async def set_once(k: str, v: str, nx: bool = False, ex: int | None = None,
                       xx: bool = False, keepttl: bool = False) -> bool:
        if xx:  # the second write: the draft id and the cleared flag
            raise ConnectionError("redis blinked")
        return await first_set(k, v, nx=nx, ex=ex, xx=xx, keepttl=keepttl)

    monkeypatch.setattr(store, "set", set_once)
    out, got = await r.card_then_act()
    assert out["draft_id"] == DRAFT and out["card_id"]
    assert isinstance(got, HTTPException), got
    assert got.status_code == 409 and rcard.DRAFT_UNCONFIRMED == "draft_unconfirmed"
    assert got.detail == {"reason": "draft_unconfirmed", "message": rc.REPLY_DRAFT_UNCONFIRMED}
    assert "nothing was sent or saved" in rc.REPLY_DRAFT_UNCONFIRMED
    assert r.draft_reads == [] and r.w.sent == []


async def test_the_happy_path_clears_the_flag_and_sends(monkeypatch) -> None:
    """Item 10: the draft id reaches the record, the flag clears, and the
    draft is still in Drafts. The act sends."""
    r = _RouteWorld(monkeypatch, in_drafts=True)
    out, got = await r.card_then_act()
    assert out["draft_id"] == DRAFT
    assert not isinstance(got, HTTPException), got
    assert got["action"] == "send" and len(r.w.sent) == 1
    assert r.draft_reads == [DRAFT]


async def test_a_record_from_before_this_release_acts_as_before(monkeypatch) -> None:
    """Item 10: a record with no flag, no time and no draft id (a card made
    before this release) checks nothing, and the act sends as before."""
    from acb_common.tenant_redis import organization_scope
    from gateway.routes.email.automation import held_card as hc

    w = _World(monkeypatch)
    card_id = "a" * 32
    old = {"owner": MEMBER, "message_id": MAIL, "added": [], "bcc": [], "held": False}
    with organization_scope(ORG):
        assert await hc._tenant_redis().set(rc._reply_record_key(card_id), json.dumps(old),
                                            nx=True, ex=60)
    out = await w.act(card_id)
    assert out["action"] == "send" and len(w.sent) == 1
    assert w.draft_reads == []


async def test_compose_reply_says_an_unconfirmed_draft_claims_no_send(monkeypatch) -> None:
    gw = _Gateway(act=_refusal("draft_unconfirmed"))
    _install(monkeypatch, gw, _member_send())
    out = await agents.compose_reply(MAIL, BOX)
    assert out == agents._REPLY_DRAFT_UNCONFIRMED
    assert out.startswith("Nothing was sent or saved") and "Do not say that the reply was sent." in out


async def test_a_card_with_no_record_writes_no_draft(monkeypatch) -> None:
    """Fix round 1, item 2: a card that adds nobody, and whose record failed,
    writes no draft. With no record the act could not check that a draft
    left Drafts. Mutation: write the draft when nobody was added → red."""
    r = _RouteWorld(monkeypatch)
    wrote: list[str] = []

    async def keep(*_a: Any, **_k: Any) -> tuple[str, bool]:
        wrote.append(DRAFT)
        return DRAFT, False

    async def no_record(*_a: Any, **_k: Any) -> str:
        return ""

    monkeypatch.setattr(rc, "_keep_card_draft", keep)
    monkeypatch.setattr(rc, "_write_reply_record", no_record)
    out = await r.w.card()
    assert wrote == [] and out["draft_id"] == "" and out["card_id"] == ""
    assert rc.DRAFT_NOT_RECORDED in out["notices"]
    assert rc.ADDED_NOT_RECORDED not in out["notices"]


async def test_a_record_that_cannot_be_read_sends_nothing(monkeypatch) -> None:
    """Fix round 1, item 2: a Redis fault at the take of the record answers
    503, because the record can name a draft that left. Mutation: read the
    fault as an empty record → the act sends → red."""
    from gateway.routes.email.automation import held_card as hc

    w = _World(monkeypatch)
    w.in_drafts = {DRAFT}
    card_id = await w.card()

    class _Down:
        async def getdel(self, _key: Any) -> Any:
            raise ConnectionError("redis is down")

    monkeypatch.setattr(hc, "_tenant_redis", lambda: _Down())
    with pytest.raises(HTTPException) as err:
        await w.act(card_id)
    assert err.value.status_code == 503 and err.value.detail == rc.RECORD_UNREAD
    assert "nothing was sent or saved" in rc.RECORD_UNREAD
    assert w.sent == [] and w.saved == []


async def test_a_draft_note_of_no_draft_or_a_bad_id_writes_nothing(monkeypatch) -> None:
    _World(monkeypatch)
    card_id = await rc._write_reply_record(USER, MAIL, [])
    await rc._note_card_draft(USER, card_id, MAIL, "")
    await rc._note_card_draft(USER, card_id, MAIL, "not-a-uuid")
    card = await rc._take_reply_record(USER, card_id, MAIL)
    assert card.draft_id == "" and card.made_at is not None


# ── 5. A failed check fails closed ──────────────────────────────────────────


async def test_a_failed_check_sends_nothing(monkeypatch) -> None:
    w = _World(monkeypatch)
    card_id = await w.card()
    w.fail = True
    with pytest.raises(HTTPException) as err:
        await w.act(card_id)
    assert err.value.status_code == 503
    assert err.value.detail == rc.REPLY_LEFT_CHECK_FAILED
    assert w.sent == [] and w.saved == []


def test_sent_after_is_strict_and_reads_no_time_as_no() -> None:
    now = datetime.now(UTC).timestamp()
    assert rc._sent_after({"sent_at": _when(1)}, now) is True
    assert rc._sent_after({"sent_at": _when(-1)}, now) is False
    assert rc._sent_after({"sent_at": _when(1)}, None) is False
    assert rc._sent_after({}, now) is False
    assert rc._sent_after({"sent_at": "not a time"}, now) is False


# ── The state route ─────────────────────────────────────────────────────────


async def test_the_state_route_reads_the_sessions_own_draft(monkeypatch) -> None:
    w = _World(monkeypatch)
    w.in_drafts = {DRAFT}
    out = await rc.reply_card_draft_state(DRAFT, USER)
    assert out == {"draft_id": DRAFT, "in_drafts": True}
    out = await rc.reply_card_draft_state("0d0d0d0d-0000-4000-8000-0000000000cc", USER)
    assert out["in_drafts"] is False
    with pytest.raises(HTTPException) as err:
        await rc.reply_card_draft_state("not-a-uuid", USER)
    assert err.value.status_code == 404


async def test_a_database_fault_of_the_state_read_is_a_503(monkeypatch) -> None:
    """Fix round 1, item 7: the docstring says 503, and so does the code. The
    card keeps its form on a 503. Mutation: let the fault escape → a 500."""
    w = _World(monkeypatch)
    w.fail = True
    with pytest.raises(HTTPException) as err:
        await rc.reply_card_draft_state(DRAFT, USER)
    assert err.value.status_code == 503 and err.value.detail == rc.DRAFT_STATE_UNREAD


def test_the_state_sql_goes_through_the_mailbox_seam() -> None:
    """The owner predicate is ``mailbox_predicate``, never a hand-written
    ``user_id``, and the read names the Drafts folders only."""
    assert "{owner}" in rc._DRAFT_STATE_SQL
    assert "user_id" not in rc._DRAFT_STATE_SQL
    assert "IN ('drafts', 'draft')" in rc._DRAFT_STATE_SQL


# ── 6. The tool says what happened ──────────────────────────────────────────


class _Gateway:
    def __init__(self, act: Any = None, state: Any = None) -> None:
        self.act = act
        self.state = state
        self.posts: list[str] = []
        self.gets: list[tuple[str, dict[str, Any]]] = []

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append(path)
        if path == "/email/reply-card":
            return {"account_id": BOX, "mailbox": f"Work · {MEMBER}", "subject": "Re: Quote",
                    "original": {"from": "Kim <kim@contoso.test>", "subject": "Quote",
                                 "received": "", "excerpt": "Can you confirm?"},
                    "recipients": {"reply": {"to": ["kim@contoso.test"], "cc": []},
                                   "reply_all": {"to": ["kim@contoso.test"], "cc": []}},
                    "body": "Hi Kim,\n\nYes.", "message_id": MAIL, "notices": [],
                    "draft_id": DRAFT}
        if path == "/email/reply-card/act":
            if isinstance(self.act, Exception):
                raise self.act
            return self.act
        raise AssertionError(f"unexpected POST {path}")

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.gets.append((path, dict(params or {})))
        if isinstance(self.state, Exception):
            raise self.state
        return self.state


def _install(monkeypatch, gw: _Gateway, answer: Any) -> None:
    monkeypatch.setattr(agents, "_post", gw.post)
    monkeypatch.setattr(agents, "_get", gw.get)
    monkeypatch.setattr(agents, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)

    async def card(**_kw: Any) -> Any:
        return answer

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)


def _member_send() -> Any:
    from acb_skills.ask_tools import ReplyAnswer

    text = _answer()
    return ReplyAnswer("rid-1", text, rcard.parse_reply_answer(text), MEMBER, THREAD)


def _refusal(reason: str, status: int = 409) -> Exception:
    return agents.GatewayError("Email POST failed", status, detail="words", reason=reason)


@pytest.mark.parametrize("reason, line", [
    ("draft_gone", "_REPLY_LEFT_DRAFTS"), ("already_replied", "_REPLY_ALREADY_SENT")])
async def test_compose_reply_gives_the_model_the_fact(monkeypatch, reason, line) -> None:
    """Mutation: let the 409 escape, or answer "Sent to" → red."""
    gw = _Gateway(act=_refusal(reason))
    _install(monkeypatch, gw, _member_send())
    out = await agents.compose_reply(MAIL, BOX)
    assert out == getattr(agents, line)
    assert out.startswith("Nothing was sent or saved") and "Sent to" not in out
    if reason == "already_replied":
        # Only this reason means that a reply went.
        assert "already sent a reply" in out and "do not send the reply again" in out
    else:
        # Fix round 1, item 3: a draft that left Drafts can be one that a rule
        # replaced. The model claims no send, and asks the member.
        assert "sent, deleted or replaced" in out
        assert "Ask the member what they want" in out
        assert "Do not say that the reply was sent." in out
        assert "already" not in out


def test_no_left_drafts_words_claim_a_send() -> None:
    """Fix round 1, item 3: the 409 words of the gateway and the closed-card
    line of the tool name all three ways out, and claim no send."""
    for words in (rc.REPLY_LEFT_DRAFTS, agents._REPLY_CLOSED_DRAFT_GONE):
        assert "sent, deleted or replaced" in words
        assert "already" not in words and "left Drafts" not in words
    assert "Do not say that the reply was sent" in agents._REPLY_CLOSED_DRAFT_GONE


async def test_compose_reply_lets_any_other_refusal_escape(monkeypatch) -> None:
    """A 409 with no reason is not a reply that left. It escapes, and the
    pack builder turns it into the shared refusal text (``pack`` mode)."""
    gw = _Gateway(act=_refusal(""))
    _install(monkeypatch, gw, _member_send())
    if F.pack_mode():
        out = await agents.compose_reply(MAIL, BOX)
        assert out not in (agents._REPLY_LEFT_DRAFTS, agents._REPLY_ALREADY_SENT)
        assert "Sent to" not in out
        return
    with pytest.raises(agents.GatewayError):
        await agents.compose_reply(MAIL, BOX)


async def test_compose_reply_never_says_discarded_for_a_draft_that_left(monkeypatch) -> None:
    """The card closed with no answer, and its draft is no longer in Drafts.
    The member can have sent it from the Email app. Mutation: answer
    ``_REPLY_DISCARDED`` there → red."""
    gw = _Gateway(state={"draft_id": DRAFT, "in_drafts": False})
    _install(monkeypatch, gw, None)
    out = await agents.compose_reply(MAIL, BOX)
    assert out == agents._REPLY_CLOSED_DRAFT_GONE
    assert "discarded and cancelled" not in out and "Nothing was sent" not in out
    assert gw.gets == [("/email/reply-card/draft-state", {"draft_id": DRAFT})]
    assert gw.posts == ["/email/reply-card"]


@pytest.mark.parametrize("state", [{"in_drafts": True}, RuntimeError("gateway down"), None])
async def test_compose_reply_keeps_the_discard_line_while_the_draft_stays(
    monkeypatch, state,
) -> None:
    gw = _Gateway(state=state)
    _install(monkeypatch, gw, None)
    out = await agents.compose_reply(MAIL, BOX)
    assert out == agents._REPLY_DISCARDED


async def test_a_revise_names_no_draft_id_of_a_draft_that_left(monkeypatch) -> None:
    """Fix round 1, item 6: the member wrote over the card, and its draft
    left Drafts. The model gets no draft id to edit or send. Mutation: hand
    the id over with no state read → red."""
    from acb_skills.ask_tools import REVISED

    gw = _Gateway(state={"draft_id": DRAFT, "in_drafts": False})
    _install(monkeypatch, gw, REVISED)
    out = await agents.compose_reply(MAIL, BOX)
    assert out.startswith(agents._CARD_REVISED_DRAFT_GONE)
    assert DRAFT not in out and "Do not say that the reply was sent." in out
    assert gw.gets == [("/email/reply-card/draft-state", {"draft_id": DRAFT})]


@pytest.mark.parametrize("state", [{"in_drafts": True}, RuntimeError("gateway down")])
async def test_a_revise_names_the_draft_id_while_the_draft_stays(monkeypatch, state) -> None:
    from acb_skills.ask_tools import REVISED

    gw = _Gateway(state=state)
    _install(monkeypatch, gw, REVISED)
    out = await agents.compose_reply(MAIL, BOX)
    assert out.startswith(agents._CARD_REVISED)
    assert f"The draft_id of this reply in Drafts is {DRAFT}." in out


def test_the_error_reads_the_machine_reason_of_a_dict_detail(monkeypatch) -> None:
    monkeypatch.setattr(agents, "_current_user_email", lambda: MEMBER)
    resp = httpx.Response(409, json={"detail": {"reason": "draft_gone",
                                                "message": rc.REPLY_LEFT_DRAFTS}})
    with pytest.raises(agents.GatewayError) as err:
        agents._raise_if_error(resp, "POST", "/email/reply-card/act")
    assert err.value.reason == "draft_gone"
    assert err.value.detail == rc.REPLY_LEFT_DRAFTS
    plain = httpx.Response(409, json={"detail": "This card answers another email."})
    with pytest.raises(agents.GatewayError) as err:
        agents._raise_if_error(plain, "POST", "/email/reply-card/act")
    assert err.value.reason == ""
