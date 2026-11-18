"""Task 3814: a chat message closes a mailbox card, and one email keeps one draft.

Production, 2026-11-18. The owner saw "Save this new email in Drafts?" and
wrote "I think you can draft it better". The chat said "Added to the reply",
and the card did not change. The cards of ``_card_refusal`` never passed
``close_on_steer``, so the run waited in the card and the steer waited for a
tool result. Each redraft then saved a second draft beside the first.

Both fixes are dark behind ``CHAT_CARD_REVISE_ORGS``
(``acb_skills.card_revise.card_revise_on``). Off, each card and each save is
the call of before, byte for byte.

The cards of the email agent, and what a chat message does to each
------------------------------------------------------------------

=============================================  ==========================  =====================
Card kind (where)                              Path                        A chat message
=============================================  ==========================  =====================
Each ``_card_refusal`` card: save a new draft   ``_confirm_destructive``    closes it (flag on),
(``_new_draft``), change a draft                                            text of its act
(``edit_draft``), snooze, labels, rules,
settings, knowledge, Reply Zero, the digest,
a purge, a mailbox change, and the rest
The outward rule card (``_outward_rule_refusal``) ``_confirm_destructive``  closes it (flag on),
                                                                            the ``card`` text
Unsubscribe, one or many senders                ``_confirm_destructive``    closes it (flag on),
                                                                            the ``unsubscribe`` text
Send a draft now (``send_draft``)               ``request_confirmation``    closes it (flag on),
                                                                            the ``send_draft`` text
A reply (``compose_reply``)                     reply form                  closes it (always on,
                                                                            the reply form default)
A new mail (``send_email`` edit card)           reply form ``kind: new``    closes it (always on)
A send now, a forward, a send later             ``close_on_steer=True``     closes it (always on)
Each held card (``_held_card``)                 ``close_on_steer=True``     closes it (always on)
A question card (``ask_tools.ask_questions``)   AG-UI ``elicitation``       nothing: see below
=============================================  ==========================  =====================

``ask_questions`` (``ask_tools.py``, path A) parks the run on its own future
with ``wait_user_future`` and watches no steer. A chat message while a
question card waits does not close it. The steer waits until the run reaches
a tool result, after the member answers the card or the card parks. It stays
so here: a question card asks for words, and the answer of the card is the
tool result. To close it on a steer changes that contract, so it needs its
own slice. The WhatsApp paths (a parked card, a run with no card channel)
never reach the steer watch, so their answers are as before.

The one-draft rule (``card_revise``): the tool names the draft that a save
replaces BEFORE the card (``plan_save``), and the card shows
``REPLACES_LINE`` and the send that it cancels. "The same email" is the same
To SET and the same subject (no "Re:" head, any case). A partial overlap of
the To people is another email. The one other way is the redraft marker of a
card that a chat message closed. After the approve, a draft that is gone or
that the member edited stays, and the save makes a new draft. A save with
files makes a new draft and then deletes the old one.

Each rule names the mutation that turns it red (R7):

1. **A ``_card_refusal`` card closes on a steer with the flag on.** Mutation:
   drop the ``close_on_steer`` of ``_confirm_destructive`` ->
   ``test_flag_on_the_gate_asks_close_on_steer``. Mutation: answer
   ``_not_done`` for REVISED -> ``test_a_closed_card_answers_the_text_of_its_act``.
2. **Off is the call of before.** Mutation: pass ``close_on_steer=False``
   with the flag off -> ``test_flag_off_the_gate_is_the_call_of_before``.
3. **Two redrafts leave one draft.** Mutation: drop the lookup in
   ``plan_save`` -> ``test_two_redrafts_leave_one_draft``.
4. **A changed subject or other people are another email.** Mutation: match
   on the To set alone -> ``test_the_same_people_with_another_subject_is_a_new_draft``.
   Mutation: match on an overlap -> ``test_a_partial_overlap_is_a_new_draft``.
5. **A save with files never keeps a file twice or a file that the member
   unticked.** Mutation: update in place with files ->
   ``test_a_save_with_files_makes_a_new_draft_and_deletes_the_old``.
6. **A draft that the member edited stays.** Mutation: drop the digest check
   -> ``test_a_draft_that_the_member_edited_is_never_written_over``.
7. **The card says what the save replaces and cancels, before the approve.**
   Mutation: drop the lines -> ``test_the_card_names_the_draft_and_the_send_it_cancels``.
8. **The record is for one member.** Mutation: drop the member from the key
   -> ``test_another_member_never_takes_the_draft``.

R8: this slice adds no SQL. The gateway read of the earlier draft is the
existing ``_fetch_message_dict``. The record lives in tenant Redis, through
the prefix wrapper. What the fence does not prove: that the model follows
the text. That needs a live eval, which costs money.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
from acb_skills import ask_tools as at
from acb_skills import card_revise as cr

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "apps" / "agents" / "agent-email-assistant" / "agents.py"

ORG = "org-card-revise"
BOX = "a0000000-0000-4000-8000-000000000001"
MEMBER = "dana@fracktal.test"
THREAD = "thread-3814"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("ea_card_revise", AGENT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


agents = _load()


# ── Fakes ────────────────────────────────────────────────────────────────────


class _Redis:
    """Tenant Redis, faked at the seam. It keeps the wire key, so a test can
    see the tenant prefix."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.fail = False
        self.reads = 0
        self.writes = 0

    async def get(self, k: Any) -> Any:
        self.reads += 1
        if self.fail:
            raise ConnectionError("down")
        return self.data.get(k.value)

    async def set(self, k: Any, value: Any, **_kw: Any) -> Any:
        self.writes += 1
        if self.fail:
            raise ConnectionError("down")
        self.data[k.value] = value
        return True


class _Mailbox:
    """The mailbox, faked at ``_put``, ``_get``, ``_post`` and ``_delete``.

    A create makes a new id. An update by ``draft_id`` changes that draft and
    ADDS its files to the files it holds, as Gmail and Outlook do. A draft
    that is gone answers 404. ``scheduled`` holds the queued sends."""

    def __init__(self) -> None:
        self.drafts: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.posts: list[str] = []
        self.gets: list[str] = []
        self.scheduled: list[dict[str, Any]] = []
        self._n = 0

    def _row(self, did: str) -> dict[str, Any]:
        d = self.drafts[did]
        return {"id": did, "account_id": BOX, "folder": d.get("folder", "drafts"),
                "subject": d.get("subject", ""), "body_text": d.get("body", ""),
                "to_addresses": [{"email": a} for a in d.get("to") or []],
                "cc_addresses": [{"email": a} for a in d.get("cc") or []],
                "bcc_addresses": [{"email": a} for a in d.get("bcc") or []],
                "has_attachments": bool(d.get("files")),
                "files": list(d.get("files", []))}

    async def put(self, path: str, body: dict[str, Any]) -> Any:
        assert path == "/email/drafts"
        self.calls.append(dict(body))
        did = body.get("draft_id")
        files = [f["filename"] for f in body.get("attachments") or []]
        if did:
            if did not in self.drafts:
                raise agents.GatewayError("Email PUT failed (404)", 404,
                                          detail="Draft not found")
            old = self.drafts[did]
            self.drafts[did] = {**{k: v for k, v in body.items() if k != "draft_id"},
                                "files": old.get("files", []) + files}
            return self._row(did)
        self._n += 1
        new = f"d{self._n:04d}"
        self.drafts[new] = {**body, "files": files}
        return self._row(new)

    async def get(self, path: str, params: Any = None) -> Any:
        self.gets.append(path)
        if path == "/email/drafts/scheduled":
            return list(self.scheduled)
        did = path.rsplit("/", 1)[-1]
        if did not in self.drafts:
            raise agents.GatewayError("Email GET failed (404)", 404)
        return self._row(did)

    async def post(self, path: str, body: Any = None) -> Any:
        self.posts.append(path)
        return {}

    async def delete(self, path: str) -> Any:
        did = path.rsplit("/", 1)[-1]
        self.deleted.append(did)
        self.drafts.pop(did, None)
        return {}

    def creates(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if not c.get("draft_id")]


@pytest.fixture()
def redis(monkeypatch: pytest.MonkeyPatch) -> _Redis:
    from acb_common.db import bind_tenant, release_tenant

    fake = _Redis()
    monkeypatch.setattr(cr, "get_tenant_redis", lambda: fake)
    token = bind_tenant(ORG)
    yield fake
    release_tenant(token)


def _flag(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_card_revise_orgs", value)


class _Cards(list):
    """The keyword arguments of each card, and the answers still to give."""

    def __init__(self) -> None:
        super().__init__()
        self.answers: list[Any] = []


@pytest.fixture()
def card(monkeypatch: pytest.MonkeyPatch) -> _Cards:
    """The card gate, faked. Each call keeps its keyword arguments, and the
    answer is the next of ``answers`` (default: closed by a chat message,
    when the card asked for it, else a no)."""
    seen = _Cards()

    async def fake(*_a: Any, **kw: Any) -> Any:
        seen.append(kw)
        if seen.answers:
            answer = seen.answers.pop(0)
            if answer is at.REVISED:
                return at._noted(at.CLOSED_BY_YOUR_MESSAGE, at.REVISED)
            return at._noted(at.CARD_APPROVED if answer else at.CARD_DECLINED, answer)
        if kw.get("close_on_steer"):
            return at._noted(at.CLOSED_BY_YOUR_MESSAGE, at.REVISED)
        return at._noted(at.CARD_DECLINED, False)

    monkeypatch.setattr(at, "request_confirmation", fake)
    monkeypatch.setattr(agents, "_card_channel_open", lambda: True)
    monkeypatch.setattr(agents, "_wa_parked", lambda: None)
    return seen


@pytest.fixture()
def tool(monkeypatch: pytest.MonkeyPatch, redis: _Redis) -> _Mailbox:
    box = _Mailbox()

    async def name(_aid: str) -> str:
        return f"Fracktal · {MEMBER}"

    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_put", box.put)
    monkeypatch.setattr(agents, "_get", box.get)
    monkeypatch.setattr(agents, "_post", box.post)
    monkeypatch.setattr(agents, "_delete", box.delete)
    monkeypatch.setattr(agents, "_run_thread", lambda: THREAD)
    monkeypatch.setattr(agents, "_current_user_email", lambda: MEMBER)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    return box


async def _stored_draft(box: _Mailbox, did: str, **fields: Any) -> None:
    """A draft that this chat saved before, in the mailbox and in the record."""
    box.drafts[did] = {"account_id": BOX, "to": RAVI, "subject": "Rates", "body": "Hi.",
                       **fields}
    await cr.note_draft(thread=THREAD, account_id=BOX, member=MEMBER, draft_id=did,
                        to=RAVI, subject="Rates", digest=cr.draft_digest(box._row(did)))


async def _save(to: list[str], subject: str, body: str) -> str:
    return await agents.edit_draft(BOX, to=to, subject=subject, body=body)


RAVI = ["ravi@contoso.test"]


# ── 1. The card closes on a steer, with the flag on ─────────────────────────


async def test_flag_on_the_gate_asks_close_on_steer(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    out = await agents._card_refusal("Snooze?", "d", "Nothing changed.")
    assert card[-1]["close_on_steer"] is True
    assert out == agents._closed_by_message("card")


async def test_flag_off_the_gate_is_the_call_of_before(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    _flag(monkeypatch, "")
    out = await agents._card_refusal("Snooze?", "d", "Nothing changed.", "ctx")
    assert card[-1] == {"title": "Snooze?", "detail": "d", "context": "ctx"}
    assert out == agents._not_done("Nothing changed.")


async def test_flag_for_another_org_is_off(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    _flag(monkeypatch, "some-other-org")
    await agents._card_refusal("Snooze?", "d", "Nothing changed.")
    assert "close_on_steer" not in card[-1]


@pytest.mark.parametrize(("closed", "note"), [
    ("new_draft", ""), ("edit_draft", "The draft_id of this draft is X."), ("card", ""),
])
async def test_a_closed_card_answers_the_text_of_its_act(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards, closed: str, note: str,
) -> None:
    _flag(monkeypatch, "*")
    out = await agents._card_refusal("t", "d", "act", closed=closed, closed_note=note)
    assert out == agents._closed_by_message(closed) + (f" {note}" if note else "")
    assert out.startswith(at.card_outcome_head(at.CLOSED_BY_YOUR_MESSAGE,
                                               "Nothing was changed by this card."))


async def test_flag_on_an_approve_and_a_no_are_as_before(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, False])
    assert await agents._card_refusal("t", "d", "The rule was not deleted.") is None
    out = await agents._card_refusal("t", "d", "The rule was not deleted.")
    assert out == agents._not_done("The rule was not deleted.")
    assert out.startswith("Declined")


async def test_flag_on_whatsapp_and_no_channel_keep_their_answers(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis, card: _Cards,
) -> None:
    """A WhatsApp park and a run with no card channel never reach the steer
    watch: the card answers False, and the tool says what it said before."""
    _flag(monkeypatch, ORG)
    card.answers.extend([False, False])
    monkeypatch.setattr(agents, "_wa_parked", lambda: "Parked: reply 1 to confirm.")
    assert await agents._card_refusal("t", "d", "act") == "Parked: reply 1 to confirm."
    monkeypatch.setattr(agents, "_wa_parked", lambda: None)
    monkeypatch.setattr(agents, "_card_channel_open", lambda: False)
    assert await agents._card_refusal("t", "d", "act") == agents._NO_CARD_CHANNEL


async def test_the_save_new_draft_card_of_the_owner_closes_on_the_steer(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """The card of the bug: "Save this new email in Drafts?"."""
    _flag(monkeypatch, ORG)
    out = await _save(RAVI, "Rates", "Hi Ravi.")
    assert card[-1]["title"] == "Save this new email in Drafts?"
    assert card[-1]["close_on_steer"] is True
    assert out == agents._closed_by_message("new_draft")
    assert tool.calls == []  # nothing was saved


async def test_the_same_card_with_the_flag_off_is_as_before(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, "")
    out = await _save(RAVI, "Rates", "Hi Ravi.")
    assert "close_on_steer" not in card[-1]
    assert cr.REPLACES_LINE not in card[-1]["context"]
    assert out == agents._not_done("No draft was saved.")
    assert tool.calls == []


async def test_a_closed_send_draft_card_answers_its_act(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    draft = "00000000-0000-4000-8000-0000000000d1"

    async def get(_path: str, *_a: Any, **_kw: Any) -> Any:
        return {"id": draft, "account_id": BOX, "folder": "drafts", "subject": "Hi",
                "to_addresses": [{"email": "ravi@contoso.test"}]}

    async def sealed(*_a: Any, **_kw: Any) -> Any:
        return "detail", "context", {"text_digest": "a" * 64}

    async def recent(_did: str, detail: str) -> str:
        return detail

    async def no_post(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("a closed card sends nothing")

    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)
    monkeypatch.setattr(agents, "_get", get)
    monkeypatch.setattr(agents, "_sealed_card", sealed)
    monkeypatch.setattr(agents, "_with_recent_reply", recent)
    monkeypatch.setattr(agents, "_post", no_post)
    out = await agents.send_draft(BOX, draft)
    assert card[-1]["close_on_steer"] is True
    assert out == agents._closed_by_message("send_draft")


# ── 2. The closed text of each act ──────────────────────────────────────────


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


@pytest.mark.parametrize("act", ["new_draft", "edit_draft", "card"])
def test_the_closed_text_names_the_three_ways(act: str) -> None:
    text = agents._closed_by_message(act)
    assert "Read them in one of three ways." in text
    assert "If they change" in text  # 1. a change: a new card of the same act
    assert "do nothing more" in text  # 2. the act ends
    assert "If they ask about something else, answer that." in text  # 3.
    assert "Do not show the card again until they ask for it." in text
    assert ";" not in text
    for sentence in _sentences(text):
        assert len(sentence.split()) <= 20, sentence


@pytest.mark.parametrize("act", ["new_draft", "edit_draft", "card"])
def test_an_act_that_is_not_a_send_never_says_nothing_was_sent(act: str) -> None:
    text = agents._closed_by_message(act)
    assert "Nothing was sent." not in text
    assert "Nothing was changed by this card." in text


@pytest.mark.parametrize("act", ["send", "forward", "send_draft", "scheduled_send"])
def test_a_send_act_keeps_the_send_words(act: str) -> None:
    assert "Nothing was sent." in agents._closed_by_message(act)


def test_the_new_draft_text_redrafts_in_place() -> None:
    text = agents._closed_by_message("new_draft")
    assert "Its tool is edit_draft." in text
    assert "call edit_draft again with no draft_id" in text
    assert "Drafts keeps one copy" in text
    assert "tell them that no draft was saved" in text


def test_the_edit_draft_text_keeps_the_same_draft() -> None:
    text = agents._closed_by_message("edit_draft")
    assert "call edit_draft again with the same draft_id" in text
    assert "Do not save a new draft." in text
    assert "tell them that the draft did not change" in text


def test_the_generic_text_names_no_tool() -> None:
    text = agents._closed_by_message("card")
    assert "Its tool is" not in text
    assert "call the same tool again" in text


# ── 3. One email keeps one draft ────────────────────────────────────────────


async def test_two_redrafts_leave_one_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True, True])
    first = await _save(RAVI, "Rates", "Hi Ravi.")
    assert "The new draft is saved" in first
    assert cr.REPLACES_LINE not in card[-1]["context"]
    second = await _save(["Ravi@Contoso.test"], "Re: rates", "Hello Ravi, the rates.")
    # The card said so BEFORE the approve: the lookup ran before the card.
    assert card[-1]["context"].startswith(cr.REPLACES_LINE)
    third = await _save(RAVI, "Rates", "Hello Ravi, the new rates.")
    assert len(tool.creates()) == 1
    assert len(tool.drafts) == 1
    [only] = tool.drafts.values()
    assert only["body"] == "Hello Ravi, the new rates."
    assert "The draft is updated" in second and "no second copy" in second
    assert "Drafts keeps one copy" in second
    assert "d0001" in third


async def test_the_same_people_with_another_subject_is_a_new_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    await _save(RAVI, "Holiday", "Hi Ravi, the holiday.")
    assert len(tool.creates()) == 2
    assert cr.REPLACES_LINE not in card[-1]["context"]


async def test_a_partial_overlap_is_a_new_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """[a] and [a, b] are two emails, with the same subject too (F2)."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    await _save([*RAVI, "kiran@contoso.test"], "Rates", "Hi both.")
    await _save(["lee@contoso.test"], "Rates", "Hi Lee.")
    assert len(tool.creates()) == 3
    assert len(tool.drafts) == 3
    assert not cr.same_mail(cr.ChatDraft("x", ("a@x.test",), "rates"),
                            ["a@x.test", "b@x.test"], "Rates")
    assert not cr.same_mail(cr.ChatDraft("x", ("a@x.test", "b@x.test"), "rates"),
                            ["a@x.test"], "Rates")
    assert cr.same_mail(cr.ChatDraft("x", ("a@x.test", "b@x.test"), "rates"),
                        ["B@x.test", "a@x.test"], "FWD: Re:  rates")


async def test_a_closed_card_marks_its_draft_for_the_redraft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards, redis: _Redis,
) -> None:
    """The member closes the card of a redraft with a message. The next save
    of the same act to the SAME people may take that draft, with a new subject."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, at.REVISED, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    out = await _save(RAVI, "Rates", "Hi Ravi, better.")
    assert out.startswith("Closed by your message")
    await _save(RAVI, "The new rates for Ravi", "Hi Ravi, the new rates.")
    assert card[-1]["context"].startswith(cr.REPLACES_LINE)
    assert len(tool.creates()) == 1 and len(tool.drafts) == 1
    # The save spent the marker.
    [raw] = redis.data.values()
    assert "marker" not in json.loads(raw)


async def test_a_marker_lives_ten_minutes(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, at.REVISED, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    await _save(RAVI, "Rates", "Hi Ravi, better.")
    now = cr.time.time()
    monkeypatch.setattr(cr.time, "time", lambda: now + cr.MARKER_TTL_S + 1)
    await _save(RAVI, "The new rates for Ravi", "Hi Ravi.")
    assert len(tool.creates()) == 2


async def test_a_marker_never_takes_a_draft_for_other_people(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """Round 2, item 3: the marker keeps the To set of the closed card."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, at.REVISED, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    await _save(RAVI, "Rates", "Hi Ravi, better.")
    await _save(["lee@contoso.test"], "Rates for Lee", "Hi Lee.")
    assert len(tool.creates()) == 2
    assert cr.REPLACES_LINE not in card[-1]["context"]


async def test_the_save_honours_the_draft_that_the_card_named(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis,
) -> None:
    """Round 4, item 1: the card showed the promise, so the save takes the
    named draft even when the answered To differs. Only its state stops it."""
    _flag(monkeypatch, ORG)
    box = _Mailbox()
    first = await box.put("/email/drafts", {"account_id": BOX, "to": RAVI,
                                            "subject": "Rates", "body": "Hi."})
    await cr.note_draft(thread=THREAD, account_id=BOX, member=MEMBER, draft_id=first["id"],
                        to=RAVI, subject="Rates", digest=cr.draft_digest(first))

    async def put(body: dict[str, Any]) -> Any:
        return await box.put("/email/drafts", body)

    async def read(did: str) -> Any:
        return box._row(did)

    saved = await cr.save_one_draft(
        put, {"account_id": BOX, "to": ["lee@contoso.test"], "subject": "Other",
              "body": "Hi Lee."},
        replaces=first["id"], thread=THREAD, member=MEMBER, act="new_mail",
        read=read, delete=box.delete)
    assert saved.outcome == cr.UPDATED and len(box.drafts) == 1
    assert box.drafts[first["id"]]["body"] == "Hi Lee."


async def test_the_card_names_the_draft_and_the_send_it_cancels(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    tool.scheduled = [{"id": "s1", "draft_id": "d0001", "status": "queued",
                       "send_at": "2026-11-19T09:00:00+05:30"}]
    out = await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    context = card[-1]["context"]
    assert context.startswith(cr.REPLACES_LINE)
    assert "This cancels the send scheduled for 2026-11-19T09:00:00+05:30." in context
    # The cancel runs after the approve, and the answer says so.
    assert tool.posts == ["/email/drafts/scheduled/s1/cancel"]
    assert "The send that was scheduled for it is cancelled." in out


async def test_a_draft_that_the_member_edited_is_never_written_over(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards, redis: _Redis,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    tool.drafts["d0001"]["body"] = "Hi Ravi. My own words."  # the Email app
    out = await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    # The card checked the draft first, so it promised nothing.
    assert cr.REPLACES_LINE not in card[-1]["context"]
    assert tool.drafts["d0001"]["body"] == "Hi Ravi. My own words."
    assert len(tool.creates()) == 2
    assert "stays in Drafts as it was" in out
    # The member's draft is never a target again: the next save takes the new one.
    await _save(RAVI, "Rates", "Hi Ravi, the rates 2.")
    assert len(tool.creates()) == 2
    assert tool.drafts["d0002"]["body"] == "Hi Ravi, the rates 2."


async def test_the_chats_own_edit_draft_is_no_hand_edit(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """``edit_draft`` of a draft that the chat saved writes the new digest, so
    the next save of the same email still takes that draft."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")

    async def no_schedules(_did: str) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(agents, "_pending_schedules", no_schedules)
    await agents.edit_draft(BOX, draft_id="d0001", body="Hi Ravi, edited in the chat.")
    await _save(RAVI, "Rates", "Hi Ravi, third.")
    assert len(tool.creates()) == 1
    assert tool.drafts["d0001"]["body"] == "Hi Ravi, third."


async def test_a_gone_draft_gives_a_new_draft_and_the_record_takes_it(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards, redis: _Redis,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    tool.drafts.clear()  # sent or deleted outside the chat
    out = await _save(RAVI, "Rates", "Hi again.")
    assert "The new draft is saved" in out and "d0002" in out
    [raw] = redis.data.values()
    assert [r["id"] for r in json.loads(raw)["drafts"]] == ["d0002"]
    await _save(RAVI, "Rates", "Hi 3.")
    assert len(tool.creates()) == 2
    assert tool.drafts["d0002"]["body"] == "Hi 3."


async def test_a_404_of_the_update_gives_a_new_draft(redis: _Redis, monkeypatch) -> None:
    """The draft went between the read and the update."""
    _flag(monkeypatch, ORG)
    box = _Mailbox()
    first = await box.put("/email/drafts", {"account_id": BOX, "to": RAVI,
                                            "subject": "Rates", "body": "Hi."})
    await cr.note_draft(thread=THREAD, account_id=BOX, member=MEMBER, draft_id=first["id"],
                        to=RAVI, subject="Rates", digest=cr.draft_digest(first))

    async def read(did: str) -> Any:
        row = box._row(did)
        box.drafts.clear()
        return row

    async def put(body: dict[str, Any]) -> Any:
        return await box.put("/email/drafts", body)

    saved = await cr.save_one_draft(
        put, {"account_id": BOX, "to": RAVI, "subject": "Rates", "body": "Hi 2."},
        replaces=first["id"], thread=THREAD, member=MEMBER, act="new_draft",
        read=read, delete=box.delete)
    assert saved.outcome == cr.CREATED and saved.result["id"] == "d0002"


async def test_flag_off_each_save_is_a_new_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards, redis: _Redis,
) -> None:
    _flag(monkeypatch, "")
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    await _save(RAVI, "Rates", "Hi 2.")
    assert len(tool.creates()) == 2
    assert all("draft_id" not in c for c in tool.calls)
    assert redis.data == {}
    assert all(cr.REPLACES_LINE not in c["context"] for c in card)
    # M8: the flag off reads no record and makes no extra GET.
    assert redis.reads == 0 and redis.writes == 0
    assert tool.gets == []


async def test_a_redis_fault_saves_as_before(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards, redis: _Redis,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    redis.fail = True
    await _save(RAVI, "Rates", "Hi Ravi.")
    await _save(RAVI, "Rates", "Hi 2.")
    assert len(tool.creates()) == 2


# ── 4. The record key ───────────────────────────────────────────────────────


async def test_the_record_key_holds_the_tenant_and_no_address(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards, redis: _Redis,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.append(True)
    await _save(RAVI, "Rates", "Hi.")
    [key] = redis.data
    assert key.startswith(f"cc:{ORG}:{cr.NAMESPACE}:")
    assert MEMBER not in key and THREAD not in key


def test_the_key_changes_with_the_member_the_thread_and_the_mailbox(redis: _Redis) -> None:
    from acb_common.tenant_redis import organization_scope

    with organization_scope(ORG):
        base = cr.record_key(THREAD, BOX, MEMBER).value
        assert cr.record_key(THREAD, BOX, "other@fracktal.test").value != base
        assert cr.record_key("thread-2", BOX, MEMBER).value != base
        assert cr.record_key(THREAD, "another-box", MEMBER).value != base
        assert cr.record_key(THREAD, BOX, MEMBER.upper()).value == base


async def test_another_member_never_takes_the_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """A shared chat and a shared mailbox: member B never writes over the
    draft of member A (F1)."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi from A.")
    monkeypatch.setattr(agents, "_current_user_email", lambda: "other@fracktal.test")
    await _save(RAVI, "Rates", "Hi from B.")
    assert len(tool.creates()) == 2
    assert cr.REPLACES_LINE not in card[-1]["context"]


async def test_another_chat_never_takes_the_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi.")
    monkeypatch.setattr(agents, "_run_thread", lambda: "another-thread")
    await _save(RAVI, "Rates", "Hi 2.")
    assert len(tool.creates()) == 2


async def test_edit_draft_of_a_draft_that_the_chat_did_not_save_joins_no_record(
    redis: _Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, ORG)
    await cr.note_draft(thread=THREAD, account_id=BOX, member=MEMBER, draft_id="reply-1",
                        to=RAVI, subject="Re: Rates", only_if_known=True)
    assert redis.data == {}


async def test_an_id_that_the_record_does_not_hold_replaces_nothing(
    redis: _Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway takes ``replaces`` from a card record. An id that this
    chat did not save is never updated or deleted."""
    _flag(monkeypatch, ORG)
    box = _Mailbox()
    other = await box.put("/email/drafts", {"account_id": BOX, "to": RAVI,
                                            "subject": "Rates", "body": "Mine."})

    async def put(body: dict[str, Any]) -> Any:
        return await box.put("/email/drafts", body)

    async def read(did: str) -> Any:
        return box._row(did)

    saved = await cr.save_one_draft(
        put, {"account_id": BOX, "to": RAVI, "subject": "Rates", "body": "New."},
        replaces=other["id"], thread=THREAD, member=MEMBER, act="new_mail",
        read=read, delete=box.delete)
    assert saved.outcome == cr.CREATED
    assert box.drafts[other["id"]]["body"] == "Mine." and box.deleted == []


# ── 5. A save with files ────────────────────────────────────────────────────


async def _file_save(box: _Mailbox, replaces: str, files: list[str], body: str) -> Any:
    async def put(payload: dict[str, Any]) -> Any:
        return await box.put("/email/drafts", payload)

    async def read(did: str) -> Any:
        return box._row(did) if did in box.drafts else None

    return await cr.save_one_draft(
        put, {"account_id": BOX, "to": RAVI, "subject": "Rates", "body": body,
              "attachments": [{"filename": f} for f in files]},
        replaces=replaces, thread=THREAD, member=MEMBER, act="new_mail",
        read=read, delete=box.delete)


async def test_a_save_with_files_makes_a_new_draft_and_deletes_the_old(
    redis: _Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No provider can drop a file of a draft, so a save that carries files
    over a draft with none makes a new draft, then deletes the old one."""
    _flag(monkeypatch, ORG)
    box = _Mailbox()
    first = await _file_save(box, "", [], "Hi.")
    did = first.result["id"]
    second = await _file_save(box, did, ["a.pdf"], "Hi 2.")
    assert second.outcome == cr.REPLACED and second.earlier == did
    assert box.deleted == [did]
    [only] = box.drafts.values()
    assert only["files"] == ["a.pdf"]
    # The order: the new draft first, then the delete.
    assert [c.get("draft_id") for c in box.calls] == [None, None]


async def test_a_failed_delete_keeps_both_and_says_so(
    redis: _Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _flag(monkeypatch, ORG)
    box = _Mailbox()
    first = await _file_save(box, "", [], "Hi.")

    async def broken(_path: str) -> Any:
        raise RuntimeError("provider down")

    box.delete = broken  # type: ignore[method-assign]
    second = await _file_save(box, first.result["id"], ["a.pdf"], "Hi 2.")
    assert second.outcome == cr.DELETE_FAILED
    assert len(box.drafts) == 2
    tail = await agents._replace_tail(second.outcome, second.earlier,
                                      agents._ChatPlan(first.result["id"]))
    assert "could not be deleted" in tail


# ── 6. The gateway saves of a new-mail card use the same rule ───────────────


async def test_the_gateway_save_takes_only_the_draft_that_the_card_named(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis,
) -> None:
    from contextlib import asynccontextmanager

    from fastapi import HTTPException
    from gateway.routes.email.automation import drafting
    from gateway.routes.email.transport import messages

    _flag(monkeypatch, ORG)
    rows: dict[str, dict[str, Any]] = {}
    calls: list[Any] = []
    deleted: list[str] = []

    async def upsert(req: Any, _user: Any) -> Any:
        calls.append(req)
        did = req.draft_id
        if did and did not in rows:
            raise HTTPException(status_code=404, detail="Draft not found")
        did = did or f"g{len(calls)}"
        rows[did] = {"id": did, "account_id": BOX, "folder": "drafts",
                     "subject": req.subject, "body_text": req.body}
        return dict(rows[did])

    @asynccontextmanager
    async def session() -> Any:
        yield object()

    async def fetch(_db: Any, did: str) -> Any:
        return dict(rows.get(did) or {})

    async def delete_message(did: str, _user: Any) -> None:
        deleted.append(did)
        rows.pop(did, None)

    monkeypatch.setattr(drafting, "_tenant_session", session)
    monkeypatch.setattr(drafting, "_fetch_message_dict", fetch)
    monkeypatch.setattr(messages, "delete_message", delete_message)
    fields = {"account_id": BOX, "to": RAVI, "cc": [], "bcc": [], "subject": "Rates",
              "body": "Hi.", "from_chat": True}
    one = await drafting.save_chat_draft(fields, object(), thread=THREAD, member=MEMBER,
                                         org=ORG, upsert=upsert)
    did = one.result["id"]
    # A card that named no draft never replaces one, even for the same email.
    two = await drafting.save_chat_draft({**fields, "body": "Hi 2."}, object(),
                                         thread=THREAD, member=MEMBER, org=ORG, upsert=upsert)
    assert two.outcome == cr.CREATED and len(rows) == 2
    # The card named it: the save updates it in place.
    three = await drafting.save_chat_draft({**fields, "body": "Hi 3."}, object(),
                                           thread=THREAD, member=MEMBER, org=ORG,
                                           replaces=did, upsert=upsert)
    assert three.outcome == cr.UPDATED and rows[did]["body_text"] == "Hi 3."
    assert drafting.replace_fields(three, {"replaces": did}) == {
        "replace_outcome": "updated", "replaced": did}
    assert drafting.replace_fields(three, {}) == {}
    # With files it saves a new draft and deletes the old one.
    four = await drafting.save_chat_draft(
        {**fields, "artifacts": [{"path": "outputs/a.pdf"}]}, object(), thread=THREAD,
        member=MEMBER, org=ORG, replaces=did, upsert=upsert)
    assert four.outcome == cr.REPLACED and deleted == [did] and did not in rows


async def test_the_gateway_save_with_the_flag_off_is_one_create(
    monkeypatch: pytest.MonkeyPatch, redis: _Redis,
) -> None:
    from gateway.routes.email.automation import drafting

    _flag(monkeypatch, "")
    calls: list[Any] = []

    async def upsert(req: Any, _user: Any) -> Any:
        calls.append(req)
        return {"id": f"g{len(calls)}"}

    fields = {"account_id": BOX, "to": RAVI, "subject": "Rates", "body": "Hi.",
              "from_chat": True}
    for _ in range(2):
        await drafting.save_chat_draft(fields, object(), thread=THREAD, member=MEMBER,
                                       org=ORG, replaces="g1", upsert=upsert)
    assert [c.draft_id for c in calls] == [None, None]
    assert redis.data == {}


async def test_the_card_record_keeps_the_named_draft_and_the_act_answers_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``replaces`` is a field of the card request, kept on the record. A held
    card that is not a new mail refuses it."""
    from gateway.routes.email.automation import held_card as hc
    from gateway.routes.email.automation import new_mail_card as nm
    from pydantic import ValidationError

    assert nm.NewMailCardRequest(account_id=BOX, replaces="d1").replaces == "d1"
    assert hc.HeldCardRequest(kind="new", account_id=BOX, to=RAVI, replaces="d1").replaces == "d1"
    with pytest.raises(ValidationError):
        hc.HeldCardRequest(kind="draft", account_id=BOX, draft_id="x", replaces="d1")
    for src in (nm, hc):
        text = Path(src.__file__).read_text(encoding="utf-8")
        assert 'replaces=str(rec.get("replaces") or "")' in text
        assert "**replace_fields(saved, rec)" in text


async def test_revise_on_and_edit_card_off_has_no_save_path(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox,
) -> None:
    """P2: Save draft lives on the form card only, and the form needs
    ``EMAIL_EDIT_CARD_ORGS``. With CHAT_CARD_REVISE_ORGS on and the edit card
    off, the new-card route answers 404, the held act takes no form answer,
    and the tool names no draft to replace."""
    from acb_auth import UserContext, UserRole
    from acb_common import get_settings
    from fastapi import HTTPException
    from gateway.routes.email.automation import new_mail_card as nm

    _flag(monkeypatch, "*")
    monkeypatch.setattr(get_settings(), "email_edit_card_orgs", "")
    user = UserContext(email=MEMBER, role=UserRole.EMPLOYEE, organization_id=ORG)
    with pytest.raises(HTTPException) as err:
        nm._off(user)
    assert err.value.status_code == 404
    held = (ROOT / "apps/services/gateway/gateway/routes/email/automation/held_card.py"
            ).read_text(encoding="utf-8")
    assert ('if (rec.get("kind") == "new" and edit_card_on(org)\n'
            '            and looks_like_new_mail_answer(req.answer)):') in held
    # The tool names no draft when no form card can show.
    await _stored_draft(tool, "d9")
    bodies: list[dict[str, Any]] = []

    async def held_card(body: dict[str, Any], *_a: Any, **_kw: Any) -> Any:
        bodies.append(body)
        return "Not shown."

    monkeypatch.setattr(agents, "_edit_card_open", lambda: False)
    monkeypatch.setattr(agents, "_held_card", held_card)
    await agents._held_new_mail(BOX, body="Hi.", to=RAVI, subject="Rates", cc=None,
                                bcc=None, reply_to_email_id=None, attachments=None,
                                reply_all=False)
    assert "replaces" not in bodies[-1]
    monkeypatch.setattr(agents, "_edit_card_open", lambda: True)
    await agents._held_new_mail(BOX, body="Hi.", to=RAVI, subject="Rates", cc=None,
                                bcc=None, reply_to_email_id=None, attachments=None,
                                reply_all=False)
    assert bodies[-1]["replaces"] == "d9"


async def test_the_new_mail_card_names_the_draft_before_the_card(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, redis: _Redis,
) -> None:
    """The tool of the new-mail edit card asks the record before the card,
    sends ``replaces`` to the gateway, and the form shows the line."""
    _flag(monkeypatch, ORG)
    await _stored_draft(tool, "d9")
    posts: list[tuple[str, dict[str, Any]]] = []
    forms: list[dict[str, Any]] = []

    async def post(path: str, body: Any = None) -> Any:
        posts.append((path, dict(body or {})))
        return {"card_id": "c" * 32, "to": RAVI, "subject": "Rates", "body": "Hi."}

    async def ask(form: dict[str, Any], *_a: Any) -> Any:
        forms.append(form)
        at._noted(at.CLOSED_BY_YOUR_MESSAGE, at.REVISED)
        return None, "REVISED", False

    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_ask_until_revised", ask)
    out = await agents._new_mail_card(BOX, "Fracktal", to=RAVI, cc=[], bcc=[],
                                      subject="Rates", body="Hi.", attachments=None,
                                      notices=[], usual="")
    assert out == "REVISED"
    assert posts[0][0] == "/email/new-card" and posts[0][1]["replaces"] == "d9"
    # Round 2, item 1: the form has Send too, so the line names Save draft.
    assert "If you press Save draft, this replaces the draft saved earlier in this chat." \
        in forms[0]["notices"]
    assert cr.REPLACES_LINE not in forms[0]["notices"]
    # A chat message closed it, so the marker names d9 for the next new mail.
    [raw] = redis.data.values()
    assert json.loads(raw)["marker"]["draft_id"] == "d9"


def test_each_chat_save_of_a_new_draft_goes_through_the_one_rule() -> None:
    """The three saves of a new chat draft: ``_new_draft`` in the agent, and
    the new-mail card saves of ``new_mail_card.py`` and ``held_card.py``."""
    agent = AGENT.read_text(encoding="utf-8")
    body = agent[agent.index("async def _new_draft("):agent.index("#: The acts of the one-draft")]
    assert "_save_chat_draft(payload, plan.replaces, _NEW_DRAFT_ACT)" in body
    assert '_put("/email/drafts"' not in body
    gw = ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "email" / "automation"
    for name in ("new_mail_card.py", "held_card.py"):
        tree = ast.parse((gw / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "upsert_draft"):
                continue
            req = node.args[0] if node.args else None
            kws = {k.arg for k in getattr(req, "keywords", [])}
            assert "from_chat" not in kws or "draft_id" in kws, (name, node.lineno)


# ── 7. The table: every card of the agent is on a known path ─────────────────


def test_every_card_of_the_agent_is_on_a_path_of_the_table() -> None:
    """A new card of the email agent goes through ``_card_refusal`` or
    ``_confirm_destructive``, or it passes ``close_on_steer`` or a reply form.
    Mutation: add a bare ``request_confirmation`` -> this test."""
    tree = ast.parse(AGENT.read_text(encoding="utf-8"))
    bare: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if name != "request_confirmation":
            continue
        kws = {k.arg for k in node.keywords}
        if kws & {"close_on_steer", "reply", None}:  # None: a ``**steer``
            continue
        bare.append(node.lineno)
    assert bare == [], f"a card with no steer path at lines {bare}"


# ── 8. Fix round 2 ──────────────────────────────────────────────────────────


async def test_a_send_on_the_form_card_leaves_the_earlier_draft_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, redis: _Redis,
) -> None:
    """Item 1: Send replaces nothing and cancels nothing. The form lines name
    Save draft only, and the answer of a Send names the draft that stays."""
    _flag(monkeypatch, ORG)
    await _stored_draft(tool, "d9")
    tool.scheduled = [{"id": "s1", "draft_id": "d9", "status": "queued",
                       "send_at": "2026-11-19T09:00:00+05:30"}]
    plan = await agents._plan_chat_draft("new_mail", BOX, RAVI, "Rates")
    lines = agents._replace_lines(plan, form=True)
    assert lines == [
        "If you press Save draft, this replaces the draft saved earlier in this chat.",
        "If you press Save draft, this cancels the send scheduled for "
        "2026-11-19T09:00:00+05:30."]
    answer = type("A", (), {"request_id": "r" * 32, "thread_id": THREAD, "answer": "{}",
                            "choice": None})()

    async def post(path: str, body: Any = None) -> Any:
        tool.posts.append(path)
        return {"action": "send", "id": "m1", "to": RAVI, "cc": []}

    async def noted(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(agents, "_post", post)
    monkeypatch.setattr(agents, "_note_sent", noted)
    out = await agents._new_mail_answered(answer, "Fracktal", "", plan)
    assert tool.posts == ["/email/new-card/act"]  # no cancel, no delete
    assert tool.deleted == [] and "d9" in tool.drafts
    assert "stays in Drafts (draft_id d9)" in out
    assert "Any send scheduled for it stays as it was." in out


async def test_a_save_on_the_form_card_takes_the_place_and_cancels(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, redis: _Redis,
) -> None:
    _flag(monkeypatch, ORG)
    await _stored_draft(tool, "d9")
    tool.scheduled = [{"id": "s1", "draft_id": "d9", "status": "queued",
                       "send_at": "2026-11-19T09:00:00+05:30"}]
    plan = await agents._plan_chat_draft("new_mail", BOX, RAVI, "Rates")
    out = await agents._form_replace_tail(
        {"action": "save", "replace_outcome": "updated", "replaced": "d9"}, plan)
    assert "Drafts keeps one copy" in out
    assert tool.posts == ["/email/drafts/scheduled/s1/cancel"]


async def test_an_earlier_draft_with_files_is_never_updated_or_deleted(
    redis: _Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 4, item 2: an update never drops a file, and a delete loses a
    file for good. So a draft with files stays, with or without files in the
    new save, and the save makes a new draft (``kept_files``)."""
    _flag(monkeypatch, ORG)
    box = _Mailbox()
    first = await _file_save(box, "", ["a.pdf"], "Hi.")
    did = first.result["id"]
    second = await _file_save(box, did, [], "Hi 2.")
    assert second.outcome == cr.KEPT_FILES
    assert box.deleted == [] and box.drafts[did]["files"] == ["a.pdf"]
    assert box.drafts[did]["body"] == "Hi."
    assert len(box.drafts) == 2


async def test_a_file_added_by_hand_keeps_the_draft(
    redis: _Redis, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(a) The record has no files, but the stored draft has one: the member
    added it in the Email app. The save never deletes or updates it."""
    _flag(monkeypatch, ORG)
    box = _Mailbox()
    first = await _file_save(box, "", [], "Hi.")
    did = first.result["id"]

    async def put(payload: dict[str, Any]) -> Any:
        return await box.put("/email/drafts", payload)

    async def read(d: str) -> Any:
        return {**box._row(d), "has_attachments": True}

    saved = await cr.save_one_draft(
        put, {"account_id": BOX, "to": RAVI, "subject": "Rates", "body": "Hi 2."},
        replaces=did, thread=THREAD, member=MEMBER, act="new_mail", read=read,
        delete=box.delete)
    assert saved.outcome == cr.KEPT_FILES and box.deleted == []
    assert box.drafts[did]["body"] == "Hi."


async def test_a_redraft_after_a_form_save_with_a_file_keeps_that_draft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards, redis: _Redis,
) -> None:
    """(b) The chat saved D with a file through the form card. A later
    ``edit_draft`` redraft to the same To and subject carries no file. The
    card says that D stays, and the answer says so too."""
    _flag(monkeypatch, ORG)
    card.answers.append(True)
    await _stored_draft(tool, "d9", files=["rates.pdf"])
    out = await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    assert card[-1]["context"].startswith("The earlier draft has files, so it stays.")
    assert cr.REPLACES_LINE not in card[-1]["context"]
    assert tool.deleted == [] and tool.drafts["d9"]["files"] == ["rates.pdf"]
    assert "The earlier draft d9 has files, so it stays in Drafts" in out
    assert len(tool.creates()) == 1


async def test_a_cc_added_by_hand_is_an_edit(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """Item 4: the digest holds the To, Cc and Bcc people too."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    tool.drafts["d0001"]["cc"] = ["boss@contoso.test"]  # the Email app
    out = await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    assert len(tool.creates()) == 2
    assert tool.drafts["d0001"]["body"] == "Hi Ravi."
    assert "stays in Drafts as it was" in out


async def test_an_edited_draft_keeps_its_schedule_and_the_card_promises_no_cancel(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """Item 5: the card checks the draft before it shows. An edited draft is
    never named, so the card promises no cancel, and the answer says that
    its send stays."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    tool.drafts["d0001"]["body"] = "Hi Ravi. My words."
    tool.scheduled = [{"id": "s1", "draft_id": "d0001", "status": "queued",
                       "send_at": "2026-11-19T09:00:00+05:30"}]
    out = await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    assert "cancels" not in card[-1]["context"]
    assert cr.REPLACES_LINE not in card[-1]["context"]
    # Round 5: the card and the answer say that its send still goes, at T.
    goes = "Its send scheduled for 2026-11-19T09:00:00+05:30 still goes."
    assert card[-1]["context"].startswith(
        "The earlier draft was changed after this chat saved it, so it stays.\n" + goes)
    assert tool.posts == []
    assert goes in out


async def test_a_5xx_of_the_schedules_never_stops_a_redraft(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """NOTED: a failed read of the queued sends is logged, the card says the
    send could not be checked, and the save still goes."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")

    async def broken(_did: str) -> Any:
        raise agents.GatewayError("Email GET failed (503)", 503)

    monkeypatch.setattr(agents, "_pending_schedules", broken)
    out = await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    assert "could not check whether a send is scheduled" in card[-1]["context"]
    assert "The draft is updated" in out
    assert "could not check whether a send was scheduled" in out
    assert len(tool.creates()) == 1


def test_the_new_draft_text_claims_no_earlier_draft() -> None:
    text = agents._closed_by_message("new_draft")
    assert "That save updates" not in text
    assert "If this chat saved this email before" in text


# ── 9. Fix round 3: the gateway records and the held form card ──────────────
#
# These drive the real card routes with the fakes of
# ``test_email_new_mail_card.py``. Each assert reads the stored save, so a
# lost record line, a lost thread or a wrong member turns it red.

from tests.unit import _held_card_fakes as F  # noqa: E402
from tests.unit import test_email_new_mail_card as NM  # noqa: E402

world = NM.world  # the fixture of the card routes

D9 = "0d0d0d0d-0000-4000-8000-0000000000d9"
CHAT = "chat-thread-1"


@pytest.fixture()
def gw_drafts(monkeypatch: pytest.MonkeyPatch, world: dict[str, Any], redis: _Redis) -> Any:
    """The draft store of the gateway: ``upsert_draft`` and the read of
    ``_fetch_message_dict``. D9 is a draft of Kim that this chat saved."""
    from contextlib import asynccontextmanager

    from gateway.routes.email.automation import drafting
    from gateway.routes.email.automation import held_card as hc

    _flag(monkeypatch, "*")
    rows: dict[str, dict[str, Any]] = {}
    saves: list[Any] = []

    def row(did: str, req: Any) -> dict[str, Any]:
        return {"id": did, "account_id": F.BOX, "folder": "drafts",
                "subject": req.subject, "body_text": req.body,
                "to_addresses": [{"email": a} for a in req.to or []],
                "cc_addresses": [{"email": a} for a in req.cc or []],
                "bcc_addresses": [{"email": a} for a in req.bcc or []]}

    async def upsert(req: Any, _user: Any) -> dict[str, Any]:
        saves.append(req)
        did = req.draft_id or f"0d0d0d0d-0000-4000-8000-{len(saves):012d}"
        rows[did] = row(did, req)
        return dict(rows[did])

    @asynccontextmanager
    async def session() -> Any:
        yield object()

    async def fetch(_db: Any, did: str) -> dict[str, Any]:
        return dict(rows.get(did) or {})

    monkeypatch.setattr(drafting, "upsert_draft", upsert)
    monkeypatch.setattr(hc, "upsert_draft", upsert)
    monkeypatch.setattr(drafting, "_tenant_session", session)
    monkeypatch.setattr(drafting, "_fetch_message_dict", fetch)

    async def seed(to: list[str], subject: str, body: str) -> None:
        req = drafting.DraftUpsertRequest(account_id=F.BOX, to=to, subject=subject, body=body)
        rows[D9] = row(D9, req)
        await cr.note_draft(thread=CHAT, account_id=F.BOX, member=F.MEMBER, draft_id=D9,
                            to=to, subject=subject, digest=cr.draft_digest(rows[D9]),
                            org=F.ORG)

    return type("G", (), {"rows": rows, "saves": saves, "seed": staticmethod(seed)})


async def test_the_new_mail_card_save_updates_the_draft_that_the_card_named(
    gw_drafts: Any,
) -> None:
    """(a) the record keeps ``replaces``, (d) the act passes the session's
    member. Mutation: drop either -> the save is a create, red."""
    await gw_drafts.seed([F.KNOWN], "Rates", "Old text.")
    card = await NM._card(card_thread_id=CHAT, replaces=D9, cc=[])
    out = await NM._act(card["card_id"], NM._answer(action="save", body="New text.", cc=()))
    [saved] = gw_drafts.saves
    assert saved.draft_id == D9 and saved.body == "New text."
    assert out["replace_outcome"] == "updated" and out["replaced"] == D9
    assert len(gw_drafts.rows) == 1


async def test_a_new_mail_card_that_named_no_draft_saves_a_new_one(gw_drafts: Any) -> None:
    await gw_drafts.seed([F.KNOWN], "Rates", "Old text.")
    card = await NM._card(card_thread_id=CHAT, cc=[])
    out = await NM._act(card["card_id"], NM._answer(action="save", body="New text.", cc=()))
    [saved] = gw_drafts.saves
    assert saved.draft_id is None and "replace_outcome" not in out
    assert gw_drafts.rows[D9]["body_text"] == "Old text."


async def test_the_held_form_save_updates_the_draft_that_the_card_named(
    gw_drafts: Any,
) -> None:
    """(b) the held record keeps ``replaces``, (c) ``_save_new`` passes the
    thread of the record. Mutation: drop either -> a create, red."""
    await gw_drafts.seed([F.KNOWN], "Rates", "Old text.")
    card = await NM._held(card_thread_id=CHAT, replaces=D9)
    out = await NM._held_act(card["card_id"], NM._answer(action="save", cc=()))
    [saved] = gw_drafts.saves
    assert saved.draft_id == D9 and saved.body == NM.TEXT
    assert out["replace_outcome"] == "updated" and out["replaced"] == D9


async def test_the_held_form_card_shows_the_replace_and_cancel_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(e) ``_held_form_card`` puts each line of the plan on the form, as the
    member sees it. Mutation: drop the loop -> red."""
    forms: list[dict[str, Any]] = []

    async def ask(form: dict[str, Any], *_a: Any) -> Any:
        forms.append(form)
        return None, "REVISED", False

    monkeypatch.setattr(agents, "_ask_until_revised", ask)
    plan = agents._ChatPlan(D9, [{"id": "s1", "draft_id": D9, "status": "queued",
                                  "send_at": "2026-11-19T09:00:00+05:30"}])
    lines = agents._replace_lines(plan, form=True)
    prep = {"card_id": "c" * 32, "form": {"kind": "new", "notices": [], "classes": {}}}
    out = await agents._held_form_card(prep, "Not sent", "act", CHAT, closed="x", lines=lines)
    assert out == "REVISED"
    assert forms[0]["notices"] == [
        "If you press Save draft, this replaces the draft saved earlier in this chat.",
        "If you press Save draft, this cancels the send scheduled for "
        "2026-11-19T09:00:00+05:30."]


# ── 10. Fix round 4 ─────────────────────────────────────────────────────────


async def _form_round(
    gw_drafts: Any, answer: str, *, before_act: Any = None,
) -> dict[str, Any]:
    await gw_drafts.seed([F.KNOWN], "Rates", "Old text.")
    card = await NM._card(card_thread_id=CHAT, replaces=D9, cc=[])
    if before_act is not None:
        before_act()
    return await NM._act(card["card_id"], answer)


async def test_a_changed_subject_on_the_form_still_takes_the_named_draft(
    gw_drafts: Any,
) -> None:
    """Item 1: the member changed the subject on the card, which promised a
    replace. The save honours the named draft."""
    out = await _form_round(gw_drafts, NM._answer(action="save", subject="New rates",
                                                  body="New text.", cc=()))
    [saved] = gw_drafts.saves
    assert saved.draft_id == D9 and saved.subject == "New rates"
    assert out["replace_outcome"] == "updated"


async def test_an_unticked_to_on_the_form_still_takes_the_named_draft(
    gw_drafts: Any,
) -> None:
    await gw_drafts.seed([F.KNOWN, F.COLLEAGUE], "Rates", "Old text.")
    card = await NM._card(card_thread_id=CHAT, replaces=D9, to=[F.KNOWN, F.COLLEAGUE],
                          cc=[])
    out = await NM._act(card["card_id"], NM._answer(action="save", to=(F.KNOWN,), cc=(),
                                                     body="New text."))
    [saved] = gw_drafts.saves
    assert saved.draft_id == D9 and saved.to == [F.KNOWN]
    assert out["replace_outcome"] == "updated"


async def test_an_expired_marker_still_takes_the_named_draft(
    gw_drafts: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plan named D by the marker. The marker runs out before the act,
    and the save still honours the card's promise."""
    now = cr.time.time()
    out = await _form_round(
        gw_drafts, NM._answer(action="save", subject="Other", body="New.", cc=()),
        before_act=lambda: monkeypatch.setattr(cr.time, "time",
                                               lambda: now + cr.MARKER_TTL_S + 1))
    [saved] = gw_drafts.saves
    assert saved.draft_id == D9 and out["replace_outcome"] == "updated"


async def test_a_promise_that_the_save_could_not_keep_is_said_plainly(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox,
) -> None:
    """Item 1: the card promised a replace and a cancel, and the save made a
    new draft. The answer names the draft and its send, which stay."""
    plan = agents._ChatPlan("d9", [{"id": "s1", "draft_id": "d9", "status": "queued",
                                   "send_at": "2026-11-19T09:00:00+05:30"}])
    out = await agents._replace_tail("created", "", plan)
    assert out == (" This is a new draft. The earlier draft d9 and its send scheduled "
                   "for 2026-11-19T09:00:00+05:30 stay as they were. Tell the member.")
    assert tool.posts == []


async def test_a_replace_never_claims_one_copy_as_sure(tool: _Mailbox) -> None:
    """Item 3: the delete of a message logs a provider fault and answers OK,
    so the answer of a replace asks the member to look."""
    out = await agents._replace_tail("replaced", "d9", agents._ChatPlan("d9"))
    assert "Drafts keeps one copy" not in out
    assert "The earlier draft d9 was deleted" in out
    assert "If the Drafts list still shows two, tell the member." in out


async def test_a_failed_delete_says_what_the_cancel_did(tool: _Mailbox, monkeypatch) -> None:
    """Item 4: the answer of a failed delete carries the cancel's own words."""
    async def cancel(_pending: Any) -> str:
        return " 1 scheduled send(s) could not be cancelled."

    monkeypatch.setattr(agents, "_cancel_schedules", cancel)
    out = await agents._replace_tail("delete_failed", "d9", agents._ChatPlan("d9", [{}]))
    assert out.endswith("Tell the member. 1 scheduled send(s) could not be cancelled.")


# ── 11. Fix round 5 ─────────────────────────────────────────────────────────

_T = "2026-11-19T09:00:00+05:30"
_GOES = f"Its send scheduled for {_T} still goes."


def _queued(did: str) -> list[dict[str, Any]]:
    return [{"id": "s1", "draft_id": did, "status": "queued", "send_at": _T}]


async def test_a_draft_gone_at_the_act_may_have_been_sent(tool: _Mailbox) -> None:
    """Item 1: a draft that is gone at the act may have gone out. The answer
    never says that it stays."""
    out = await agents._replace_tail("created", "d9", agents._ChatPlan("d9", _queued("d9")))
    assert out == (" The earlier draft d9 is no longer in Drafts. It may have been sent "
                   "or deleted. Check before you send this one.")
    # No record (earlier ""): the draft is still there, with its send.
    rest = await agents._replace_tail("created", "", agents._ChatPlan("d9", _queued("d9")))
    assert "stay as they were" in rest


async def test_a_draft_with_files_names_its_send_that_still_goes(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """Item 2: the files state reads the queued sends, so the card and the
    answer say that the old text still goes at T."""
    _flag(monkeypatch, ORG)
    card.answers.append(True)
    await _stored_draft(tool, "d9", files=["rates.pdf"])
    tool.scheduled = _queued("d9")
    out = await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    assert card[-1]["context"].startswith(
        "The earlier draft has files, so it stays.\n" + _GOES)
    assert _GOES in out and tool.posts == []


async def test_a_file_added_by_hand_before_save_names_the_send(tool: _Mailbox) -> None:
    """The draft got a file after the card: the act keeps it, and the answer
    names its send from the plan."""
    out = await agents._replace_tail("kept_files", "d9", agents._ChatPlan("d9", _queued("d9")))
    assert "The earlier draft d9 has files, so it stays" in out
    assert out.endswith(_GOES)


async def test_a_5xx_of_the_sends_of_a_kept_draft_is_said(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    _flag(monkeypatch, ORG)
    card.answers.append(True)
    await _stored_draft(tool, "d9", files=["rates.pdf"])

    async def broken(_did: str) -> Any:
        raise agents.GatewayError("Email GET failed (503)", 503)

    monkeypatch.setattr(agents, "_pending_schedules", broken)
    await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    assert card[-1]["context"].startswith(
        "The earlier draft has files, so it stays.\n" + agents._SCHEDULE_UNREAD)


# ── 12. The verifier's survivors (on 72ea8818e) ─────────────────────────────


async def test_a_subject_changed_by_hand_is_an_edit(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """M5c: the digest holds the subject. A subject that the member changed
    in the Email app keeps the draft, and the save makes a new one."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    tool.drafts["d0001"]["subject"] = "Rates, my words"
    out = await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    assert len(tool.creates()) == 2
    assert tool.drafts["d0001"]["subject"] == "Rates, my words"
    assert tool.drafts["d0001"]["body"] == "Hi Ravi."
    assert card[-1]["context"].startswith(agents._KEPT_EDITED_LINE)
    assert "was changed after it was saved" in out


async def test_edit_draft_of_a_reply_or_member_draft_joins_no_record(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards, redis: _Redis,
) -> None:
    """M18: ``edit_draft`` of a draft that this chat did not save (a reply
    draft, or the member's own) never joins the record, so a later new draft
    never writes over it."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    tool.drafts["reply-1"] = {"account_id": BOX, "to": RAVI, "subject": "Rates",
                              "body": "Hi."}
    await agents.edit_draft(BOX, draft_id="reply-1", body="Hi Ravi, edited.")
    assert redis.data == {}
    await _save(RAVI, "Rates", "Hi Ravi, a new mail.")
    assert len(tool.creates()) == 1
    assert tool.drafts["reply-1"]["body"] == "Hi Ravi, edited."


async def test_an_edit_between_card_and_approve_cancels_nothing(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, redis: _Redis,
) -> None:
    """M16 and M26: the member edits the draft while the card is open. The
    save keeps it, cancels no send, says its send still goes, and the draft
    leaves the record."""
    _flag(monkeypatch, ORG)
    monkeypatch.setattr(agents, "_card_channel_open", lambda: True)
    monkeypatch.setattr(agents, "_wa_parked", lambda: None)

    async def approve_after_a_hand_edit(*_a: Any, **_kw: Any) -> Any:
        if "d0001" in tool.drafts:
            tool.drafts["d0001"]["body"] = "Hi Ravi. My own words."
        return at._noted(at.CARD_APPROVED, True)

    async def plain_approve(*_a: Any, **_kw: Any) -> Any:
        return at._noted(at.CARD_APPROVED, True)

    monkeypatch.setattr(at, "request_confirmation", plain_approve)
    await _save(RAVI, "Rates", "Hi Ravi.")
    tool.scheduled = [{"id": "s1", "draft_id": "d0001", "status": "queued",
                       "send_at": "2026-11-19T09:00:00+05:30"}]
    monkeypatch.setattr(at, "request_confirmation", approve_after_a_hand_edit)
    out = await _save(RAVI, "Rates", "Hi Ravi, the rates.")
    assert tool.posts == []  # no cancel
    assert tool.drafts["d0001"]["body"] == "Hi Ravi. My own words."
    assert "Its send scheduled for 2026-11-19T09:00:00+05:30 still goes." in out
    [raw] = redis.data.values()
    assert [r["id"] for r in json.loads(raw)["drafts"]] == ["d0002"]


async def test_a_draft_that_left_drafts_reads_gone(
    monkeypatch: pytest.MonkeyPatch, tool: _Mailbox, card: _Cards,
) -> None:
    """M30: a row whose folder is Sent is gone, even with the same digest."""
    _flag(monkeypatch, ORG)
    card.answers.extend([True, True])
    await _save(RAVI, "Rates", "Hi Ravi.")
    tool.drafts["d0001"]["folder"] = "sent"
    await _save(RAVI, "Rates", "Hi Ravi, again.")
    assert cr.REPLACES_LINE not in card[-1]["context"]
    assert agents._KEPT_EDITED_LINE not in card[-1]["context"]
    assert len(tool.creates()) == 2 and tool.drafts["d0001"]["body"] == "Hi Ravi."
    row = tool._row("d0001")
    target = cr.ChatDraft("d0001", tuple(RAVI), "rates", cr.draft_digest(row))
    assert cr._state(row, target) == cr.GONE
