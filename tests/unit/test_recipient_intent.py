"""Recipient intent on the reply card (owner request of 2026-10-24).

A real run took 11 cards, dropped the customer's Cc, and sent the customer
two mails. ``email_app_master_plan.md`` §21.12 records the fixes. This file
fences them (R7), each with the mutation that turns it red:

* item 1 — ``test_a_person_on_the_received_mail_is_thread`` and
  ``test_a_lookalike_person_on_the_thread_stays_new``: the classifier.
* item 2 — ``test_compose_reply_cc_puts_every_person_on_one_card`` and
  ``test_the_act_takes_only_a_person_that_the_card_recorded``.
* item 7 — ``test_a_usual_colleague_is_pre_added_only_when_org``.
* item 8 — ``test_each_person_has_words``.
* item 9 — ``test_the_draft_names_exactly_the_ticked_start``.
* item 10 — ``test_a_cc_that_edit_draft_added_stays_on_the_next_card``.
* item 11 — ``test_one_list_and_the_act_ignores_the_reply_choice`` and
  ``test_a_member_on_bcc_only_starts_with_the_sender_alone``.
* the replay — ``test_the_real_scenario_is_one_card_and_one_send``.

No test sends a mail. The R8 half is ``test_email_recipient_intent_r8.py``.
"""
from __future__ import annotations

import contextlib
import json
import types
from typing import Any

import pytest
from acb_skills import held_card as H
from acb_skills import reply_card as rcard
from fastapi import HTTPException
from gateway.routes.email.automation import held_card as hc
from gateway.routes.email.automation import reply_card as rc

from tests.unit import _held_card_fakes as F

ORG = F.ORG
MEMBER = "owner@fracktal.in"
SAGAR = "sagar.kg@jainuniversity.ac.in"
ANANDA = "ananda.babu@jainuniversity.ac.in"
KIRAN = "kirankumar@fracktal.in"
VEENA = "veena.nair@fracktal.in"
MAIL = "0b0b0b0b-0000-4000-8000-00000000000b"
BOX = "0a0a0a0a-0000-4000-8000-00000000000a"
THREAD = "th-jain-1"
USER = types.SimpleNamespace(email=MEMBER, organization_id=ORG)


def _mail(*, to: list[str] | None = None, cc: list[str] | None = None,
          folder: str = "inbox") -> rc._Mail:
    row = types.SimpleNamespace(
        account_id=BOX, provider_message_id="p1", thread_id=THREAD,
        subject="Meeting on Wednesday",
        from_address={"name": "Dr Sagar", "email": SAGAR},
        to_addresses=[{"email": a} for a in (to if to is not None else [MEMBER])],
        cc_addresses=[{"email": a} for a in (cc if cc is not None else [ANANDA])],
        received_at=None, snippet="Can we meet on Wednesday?", body_text="",
        folder=folder)
    me = types.SimpleNamespace(address=MEMBER, label="Work",
                               self_addresses=frozenset({MEMBER}))
    return rc._Mail(row, me, [])


class _Db:
    """The statements of the reply card, answered by their text."""

    def __init__(self) -> None:
        self.org = {KIRAN, VEENA, MEMBER}
        self.sent: set[str] = set()
        self.participants: list[str] = [MEMBER, ANANDA]
        self.org_domains = ["fracktal.in"]
        self.draft: dict[str, Any] | None = None
        self.usual: list[tuple[str, int, bool]] = []

    async def execute(self, sql: Any, params: dict[str, Any] | None = None) -> Any:
        q, p = str(sql), dict(params or {})

        def rows(items: list[Any]) -> Any:
            return types.SimpleNamespace(fetchall=lambda: items,
                                         fetchone=lambda: items[0] if items else None)

        if q == hc.CLASSIFY_SQL:
            return rows([types.SimpleNamespace(addr=a, is_org=a in self.org,
                                               is_sent=a in self.sent)
                         for a in p["wanted"]])
        if q == hc.THREAD_PARTICIPANTS_SQL:
            return rows([types.SimpleNamespace(addr=a) for a in self.participants])
        if q == hc.ORG_DOMAINS_SQL:
            return rows([types.SimpleNamespace(d=d) for d in self.org_domains])
        if q == rc.THREAD_DRAFT_PEOPLE_SQL:
            return rows([types.SimpleNamespace(**self.draft)] if self.draft else [])
        if q == rc.USUAL_CC_SQL:
            return rows([types.SimpleNamespace(addr=a, n=n, in_thread=t)
                         for a, n, t in self.usual])
        raise AssertionError(f"unexpected SQL: {q[:80]}")


class _World:
    def __init__(self, monkeypatch, mail: rc._Mail | None = None) -> None:
        self.db = _Db()
        self.mail = mail or _mail()
        self.drafts: list[tuple[list[str], list[str]]] = []
        self.sent: list[Any] = []
        self.redis = F.install_redis(monkeypatch)

        @contextlib.asynccontextmanager
        async def session():
            yield self.db

        async def load_mail(message_id: str, owner: str) -> Any:
            return self.mail

        async def keep(mail: Any, owner: str, to: list[str], cc: list[str],
                       subject: str, body: str, *, prior: Any = None) -> tuple[str, bool]:
            self.drafts.append((list(to), list(cc)))
            return "local-draft-1", False

        async def no_record(_mail: Any) -> list[str]:
            return []

        async def send(req: Any, background: Any, user: Any) -> dict[str, Any]:
            self.sent.append(req)
            return {"id": "sent-1"}

        monkeypatch.setattr(rc, "_tenant_session", session)
        monkeypatch.setattr(rc, "_load_mail", load_mail)
        monkeypatch.setattr(rc, "_keep_card_draft", keep)
        monkeypatch.setattr(rc, "_chat_record", no_record)
        monkeypatch.setattr(rc, "send_email", send)

    async def card(self, **kw: Any) -> dict[str, Any]:
        return await rc.reply_card(
            rc.ReplyCardRequest(message_id=MAIL, draft="Wednesday 11am works.", **kw), USER)

    async def act(self, out: dict[str, Any], *, to: list[str], cc: list[str],
                  reply_all: bool = True, action: str = "send") -> dict[str, Any]:
        rid = out.get("card_id") or "f" * 32
        text = "APPROVE " + json.dumps({"reply": {
            "action": action, "reply_all": reply_all, "body": "Wednesday 11am works.",
            "to": to, "cc": cc, "message_id": MAIL}})
        await rcard.record_reply_answer(ORG, rid, MEMBER, text, "chat-1")
        return await rc.reply_card_act(
            rc.ReplyCardActRequest(message_id=MAIL, card_request_id=rid,
                                   card_thread_id="chat-1", answer=text),
            types.SimpleNamespace(), USER)


# ── Item 1: the classifier ───────────────────────────────────────────────────


async def test_a_person_on_the_received_mail_is_thread() -> None:
    """Mutation: drop the ``thread_people`` arm → Ananda is new → red."""
    db = F.FakeDb()
    db.classes = {}
    got = await hc.classify_addresses(
        db, F.MEMBER, [SAGAR, ANANDA], thread_senders=[SAGAR], thread_people=[ANANDA],
        org_domains=["fracktal.in"])
    assert got == {SAGAR: "thread", ANANDA: "thread"}
    assert H.CLASS_THREAD in H.DEFAULT_TICKED


async def test_a_lookalike_person_on_the_thread_stays_new() -> None:
    """The owner's carve-out. A Cc on a look-alike of the sender's domain,
    or of an organization domain, is new. Mutation: skip the guard → red."""
    db = F.FakeDb()
    db.classes = {}
    fake_jain = "ananda@jainuniversity.ac.ln"
    fake_org = "cfo@fracktai.in"
    got = await hc.classify_addresses(
        db, F.MEMBER, [fake_jain, fake_org], thread_senders=[SAGAR],
        thread_people=[fake_jain, fake_org], org_domains=["fracktal.in"])
    assert got == {fake_jain: "new", fake_org: "new"}
    assert hc.thread_person_guard(fake_org, [SAGAR], ["fracktal.in"]) == "fracktal.in"


def test_the_participants_sql_reads_received_mail_only() -> None:
    assert "NOT IN ('drafts', 'draft', 'sent')" in hc.THREAD_PARTICIPANTS_SQL
    assert "to_addresses" in hc.THREAD_PARTICIPANTS_SQL
    assert "cc_addresses" in hc.THREAD_PARTICIPANTS_SQL
    assert "bcc_addresses" not in hc.THREAD_PARTICIPANTS_SQL


async def test_the_held_draft_card_classes_a_thread_person_thread(monkeypatch) -> None:
    """The send_draft card reads the same rule. Mutation: drop
    ``thread_participants`` from ``_draft_card`` → the stranger row is new."""
    db = F.install_db(monkeypatch)
    F.install_redis(monkeypatch)
    F.flag_on(monkeypatch)
    db.participants = [F.STRANGER]
    db.org_domains = ["acme.test"]
    out = await hc.held_card(hc.HeldCardRequest(kind="draft", account_id=F.BOX,
                                                draft_id=F.DRAFT), F.user())
    by = {r["label"]: r for r in out["rows"]}
    assert by[f"Cc: {F.STRANGER}"]["class"] == "thread"
    assert by[f"Cc: {F.STRANGER}"]["checked"] is True


# ── Items 2, 8, 9 and 11: the reply card ─────────────────────────────────────


async def test_the_real_scenario_is_one_card_and_one_send(monkeypatch) -> None:
    """The replay of 2026-10-24. Mail From Dr Sagar To the member, Cc Ananda.
    "Reply confirming Wednesday 11am, copy Kiran and Veena, send it" is ONE
    card: To Sagar, Cc Ananda, Kiran and Veena, each ticked, and ONE send."""
    w = _World(monkeypatch)
    F.flag_on(monkeypatch)
    out = await w.card(cc=[KIRAN, VEENA], untick_new=True)
    assert out["recipients"]["reply_all"] == {"to": [SAGAR], "cc": [ANANDA, KIRAN, VEENA]}
    assert out["ticked"]["reply_all"] == {"to": [SAGAR], "cc": [ANANDA, KIRAN, VEENA]}
    assert w.drafts == [([SAGAR], [ANANDA, KIRAN, VEENA])]
    assert out["card_id"]
    sent = await w.act(out, to=[SAGAR], cc=[ANANDA, KIRAN, VEENA])
    assert sent["action"] == "send"
    [mail] = w.sent
    assert mail.to == [SAGAR] and mail.cc == [ANANDA, KIRAN, VEENA]


async def test_compose_reply_cc_puts_every_person_on_one_card(monkeypatch) -> None:
    """The tool passes ``cc`` to the card route and the record id to the card.
    Mutation: drop ``request_id`` → the act cannot read the record → red."""
    agents = F.load_email_agent("ea_recipient_intent")
    posts: list[tuple[str, dict[str, Any]]] = []
    cards: list[dict[str, Any]] = []

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        posts.append((path, body))
        return {"message_id": MAIL, "card_id": "c" * 32, "reply_all": True, "body": "Hi",
                "recipients": {"reply": {"to": [SAGAR], "cc": [KIRAN]},
                               "reply_all": {"to": [SAGAR], "cc": [ANANDA, KIRAN]}},
                "ticked": {"reply": {"to": [SAGAR], "cc": [KIRAN]},
                           "reply_all": {"to": [SAGAR], "cc": [KIRAN]}},
                "marks": {ANANDA: H.thread_mark("Dr Sagar")}}

    async def confirm(**kw: Any) -> None:
        cards.append(kw)
        return None

    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr("acb_skills.ask_tools.reply_card_channel_open", lambda: True)
    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", confirm)
    await agents.compose_reply(MAIL, BOX, cc=[KIRAN])
    assert posts[0] == ("/email/reply-card", {"message_id": MAIL, "cc": [KIRAN]})
    assert len(cards) == 1 and cards[0]["request_id"] == "c" * 32
    assert f"{ANANDA} will not get this." in cards[0]["detail"]
    assert cards[0]["reply"]["marks"] == {ANANDA: "on Dr Sagar's original email"}


async def test_a_held_chat_with_the_switch_off_refuses_cc(monkeypatch) -> None:
    agents = F.load_email_agent("ea_recipient_intent_off")
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr("acb_skills.ask_tools.reply_card_channel_open", lambda: True)

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("no card route call")

    monkeypatch.setattr(agents, "_post", post)
    assert await agents.compose_reply(MAIL, BOX, cc=[F.STRANGER]) == (
        agents._HELD_REPLY_CC_REFUSED)


async def test_a_new_added_person_starts_unticked_in_a_held_chat(monkeypatch) -> None:
    w = _World(monkeypatch)
    F.flag_on(monkeypatch)
    out = await w.card(cc=[F.STRANGER], untick_new=True)
    assert F.STRANGER in out["recipients"]["reply_all"]["cc"]
    assert F.STRANGER not in out["ticked"]["reply_all"]["cc"]
    assert out["classes"][F.STRANGER] == "new"
    assert out["marks"][F.STRANGER].startswith(H.NEW_MARK)
    # Outside a held chat the member asked for the person, so it starts ticked.
    F.flag_off(monkeypatch)
    out = await w.card(cc=[F.STRANGER])
    assert F.STRANGER in out["ticked"]["reply_all"]["cc"]


async def test_the_act_takes_only_a_person_that_the_card_recorded(monkeypatch) -> None:
    """An added address that the record does not hold sends nothing, and a
    record of another mail adds nobody. Mutation: read ``added`` from the
    answer → red."""
    w = _World(monkeypatch)
    out = await w.card(cc=[KIRAN])
    with pytest.raises(HTTPException) as err:
        await w.act(out, to=[SAGAR], cc=[ANANDA, KIRAN, "spy@evil.example"])
    assert err.value.status_code == 409 and w.sent == []
    out = await w.card(cc=[KIRAN])
    # The record is read once: a second act on the same card id adds nobody.
    await w.act(out, to=[SAGAR], cc=[KIRAN])
    with pytest.raises(HTTPException):
        await w.act(out, to=[SAGAR], cc=[KIRAN])
    assert [m.cc for m in w.sent] == [[KIRAN]]


async def test_a_bad_cc_address_is_a_422(monkeypatch) -> None:
    w = _World(monkeypatch)
    with pytest.raises(HTTPException) as err:
        await w.card(cc=["kiran​@fracktal.in"])
    assert err.value.status_code == 422


async def test_one_list_and_the_act_ignores_the_reply_choice(monkeypatch) -> None:
    """Item 11. The card shows one list, and an answer that says Reply still
    sends to each person that the member ticked from it."""
    w = _World(monkeypatch)
    out = await w.card()
    assert out["ticked"]["reply_all"] == {"to": [SAGAR], "cc": [ANANDA]}
    await w.act(out, to=[SAGAR], cc=[ANANDA], reply_all=False)
    assert w.sent[0].cc == [ANANDA]


async def test_a_member_on_bcc_only_starts_with_the_sender_alone(monkeypatch) -> None:
    """Mutation: drop the ``bcc_only`` start → Ananda starts ticked → red."""
    w = _World(monkeypatch, _mail(to=["team@jainuniversity.ac.in"], cc=[ANANDA]))
    out = await w.card()
    assert out["ticked"]["reply_all"] == {"to": [SAGAR], "cc": []}
    assert rc.BCC_ONLY_NOTICE in out["notices"]
    assert w.drafts == [([SAGAR], [])]


async def test_the_draft_names_exactly_the_ticked_start(monkeypatch) -> None:
    """Item 9: an explicit ``reply_all`` true and the held unticked start
    give the same lists to the draft and to the card."""
    w = _World(monkeypatch)
    F.flag_on(monkeypatch)
    w.db.participants = []  # Ananda is not a thread person here: new
    w.mail = _mail(folder="sent")
    out = await w.card(reply_all=True, untick_new=True)
    start = out["ticked"]["reply_all"]
    assert out["draft_recipients"] == start
    assert w.drafts == [(start["to"], start["cc"])]


async def test_each_person_has_words(monkeypatch) -> None:
    w = _World(monkeypatch)
    w.db.usual = [(VEENA, 3, False)]
    out = await w.card(cc=[KIRAN])
    assert out["marks"][ANANDA] == "on Dr Sagar's original email"
    assert out["marks"][KIRAN] == H.ASKED_MARK
    assert out["marks"][VEENA] == H.USUAL_COPY_MARK
    assert SAGAR not in out["marks"]


# ── Item 7: who the member usually copies ────────────────────────────────────


async def test_a_usual_colleague_is_pre_added_only_when_org(monkeypatch) -> None:
    """Mutation: pre-add a ``sent`` person too → the stranger is on → red."""
    w = _World(monkeypatch)
    w.db.sent = {"friend@gmail.test"}
    w.db.usual = [(VEENA, 2, False), (KIRAN, 1, True), ("friend@gmail.test", 5, False),
                  ("once@fracktal.in", 1, False)]
    w.db.org |= {"once@fracktal.in"}
    out = await w.card()
    assert out["recipients"]["reply_all"]["cc"] == [ANANDA, KIRAN, VEENA]
    assert out["ticked"]["reply_all"]["cc"] == [ANANDA, KIRAN, VEENA]
    assert any(n.startswith(f"Added {KIRAN}, {VEENA}.") and "jainuniversity.ac.in" in n
               for n in out["notices"])


def test_the_usual_sql_counts_only_the_mailboxs_own_sent_mail() -> None:
    assert "LOWER(em.from_address->>'email') = LOWER(ea.email_address)" in rc.USUAL_CC_SQL
    assert "ea.user_id = :uid" in rc.USUAL_CC_SQL


# ── Item 10: the owner's report — a Cc that edit_draft added ─────────────────


async def test_a_cc_that_edit_draft_added_stays_on_the_next_card(monkeypatch) -> None:
    """The bug. A card answered Reply, then edit_draft put Kiran on the draft
    in Cc. The next reply card derived from the mail alone, so its Send went
    to Sagar only. Now the draft's Cc is an added, ticked person, and the
    send carries it. Mutation: skip ``_draft_people`` → red."""
    w = _World(monkeypatch)
    w.db.draft = {"cc_addresses": json.dumps([{"email": KIRAN}]),
                  "bcc_addresses": json.dumps([{"email": VEENA}])}
    out = await w.card(reply_all=False)
    assert out["ticked"]["reply_all"] == {"to": [SAGAR], "cc": [KIRAN]}
    assert rc.DRAFT_BCC_NOTICE in out["notices"]
    await w.act(out, to=[SAGAR], cc=[KIRAN])
    assert w.sent[0].to == [SAGAR] and w.sent[0].cc == [KIRAN]


async def test_the_held_send_draft_card_sends_exactly_the_ticked_rows(monkeypatch) -> None:
    """The other half of the report: a send_draft card with a Cc and a Bcc
    ticked sends both. Mutation: send the row lists of the record in place
    of the ticked rows → red when a row is unticked."""
    db = F.install_db(monkeypatch)
    F.install_redis(monkeypatch)
    sends = F.install_sends(monkeypatch)
    F.flag_on(monkeypatch)
    db.draft["bcc_addresses"] = json.dumps([{"email": F.COLLEAGUE.replace("geo", "ana")}])
    db.classes[F.COLLEAGUE.replace("geo", "ana")] = (True, False)
    out = await hc.held_card(hc.HeldCardRequest(kind="draft", account_id=F.BOX,
                                                draft_id=F.DRAFT), F.user())
    ids = [r["id"] for r in out["rows"] if r["checked"]]
    answer = F.rows_answer(*ids)
    await F.record_answer(out["card_id"], answer)
    res = await hc.held_card_act(hc.HeldCardActRequest(
        card_id=out["card_id"], card_thread_id="chat-thread-1", answer=answer),
        types.SimpleNamespace(), F.user())
    [req] = sends.sent
    assert req.expect.cc == [F.COLLEAGUE] and req.expect.bcc == ["ana@acme.test"]
    assert res["cc"] == [F.COLLEAGUE] and res["bcc"] == ["ana@acme.test"]
