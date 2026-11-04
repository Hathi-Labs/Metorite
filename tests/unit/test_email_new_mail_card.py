"""The one email card of a new mail (``email_app_master_plan.md`` §18.10).

The owner asked on 2026-11-03 to edit a draft in the chat. A new mail shows
ONE card: the member ticks the people, edits the subject and the text, and
presses Send, Save draft or Discard. The card is the reply card of §18.4
with ``kind: "new"``.

What this file fences (R7). Each test names the mutation that turns it red.

1. The answer parses strictly, and a reply answer and a new-mail answer never
   parse as each other. ``test_the_answer_*``.
2. The act sends the ANSWERED subject and text, to the ticked people of the
   RECORD, with the RECORDED files, once. ``test_the_act_*``.
3. The act refuses a person that the record does not hold, hidden text, a
   bad subject, a second answer, another member and a missing record. Each
   refusal sends and saves nothing. ``test_*_sends_nothing``.
4. Save draft writes a NEW draft with the answered text, and sends nothing.
5. The held path (§21) keeps its rules on the form: a new address starts
   unticked, a look-alike word and a long text are refused. ``test_held_*``.
6. ``send_email`` draws the card where a run can draw it, posts the exact
   answer, and tells the model the text that went. A run that cannot draw
   it keeps the old card. ``test_send_email_*``.

The database, Redis and the provider are fakes. No test sends a mail.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from acb_skills import ask_tools
from acb_skills import reply_card as rcard
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.automation import drafting
from gateway.routes.email.automation import held_card as hc
from gateway.routes.email.automation import new_mail_card as nm
from pydantic import ValidationError

from tests.unit import _held_card_fakes as F

REPO = Path(__file__).resolve().parents[2]
TEXT = "Hi Kim,\n\nThe rates are attached."
#: A bidi override and Unicode tag characters, built with chr().
BIDI = "Hi " + chr(0x202E) + "evil"
TAGS = "Hi" + "".join(chr(0xE0000 + ord(c)) for c in "acct")
#: A Cyrillic letter a (U+0430) inside a Latin word (H-393).
LOOKALIKE_WORD = "Please p" + chr(0x0430) + "y the invoice."


def edit_card(monkeypatch, value: str) -> None:
    """``EMAIL_EDIT_CARD_ORGS`` for this test only: "" is off, "*" is on."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "email_edit_card_orgs", value)


@pytest.fixture()
def world(monkeypatch) -> dict[str, Any]:
    F.flag_on(monkeypatch)
    edit_card(monkeypatch, "*")
    monkeypatch.setattr(nm, "ANSWER_WAIT_S", 0.0)
    monkeypatch.setattr(hc, "ANSWER_WAIT_S", 0.0)
    redis = F.install_redis(monkeypatch)
    db = F.install_db(monkeypatch)
    sends = F.install_sends(monkeypatch)
    monkeypatch.setattr(nm, "_tenant_session", hc._tenant_session)
    monkeypatch.setattr(nm, "resolve_self", hc.resolve_self)
    saves: list[Any] = []

    async def upsert(req: Any, user: Any) -> dict[str, Any]:
        saves.append(req)
        return {"id": "draft-new-1"}

    monkeypatch.setattr(drafting, "upsert_draft", upsert)
    monkeypatch.setattr(hc, "upsert_draft", upsert)
    return {"redis": redis, "db": db, "sends": sends, "saves": saves}


def _answer(*, action: str = "send", subject: str = "Rates", body: str = TEXT,
            to: tuple[str, ...] = (F.KNOWN,), cc: tuple[str, ...] = (F.COLLEAGUE,),
            bcc: tuple[str, ...] = (), **extra: Any) -> str:
    mail = {"kind": "new", "action": action, "subject": subject, "body": body,
            "to": list(to), "cc": list(cc), "bcc": list(bcc), **extra}
    return "APPROVE " + json.dumps({"reply": mail})


async def _card(**kw: Any) -> dict[str, Any]:
    body = {"account_id": F.BOX, "to": [F.KNOWN], "cc": [F.COLLEAGUE], "subject": "Rates",
            "body": TEXT, **kw}
    return await nm.new_mail_card(nm.NewMailCardRequest(**body), F.user())


async def _act(card_id: str, answer: str, *, who: str = F.MEMBER,
               thread: str = "chat-thread-1", record: bool = True) -> dict[str, Any]:
    if record:
        await F.record_answer(card_id, answer, who=who, thread=thread)
    req = nm.NewMailActRequest(card_id=card_id, card_thread_id=thread, answer=answer)
    return await nm.new_mail_card_act(req, BackgroundTasks(), F.user(who))


# ── 1. The answer ─────────────────────────────────────────────────────────────


def test_the_answer_parses_the_members_choice() -> None:
    got = rcard.parse_new_mail_answer(_answer(subject="New", body="Edited."))
    assert got == rcard.NewMailChoice(action="send", subject="New", body="Edited.",
                                      to=(F.KNOWN,), cc=(F.COLLEAGUE,), bcc=())


@pytest.mark.parametrize("bad", [
    _answer(attachments=["outputs/x.pdf"]),                  # an extra key adds a file
    _answer(files="file-0"),                                 # files is a list of ids
    _answer(files=["file-0", "file-0"]),                     # a file named twice
    _answer(files=["x" * 65]),                               # an id that is too long
    _answer(message_id="m-1"),                               # an extra key
    _answer(to=()),                                          # no To
    _answer(body="   "),                                     # no text
    _answer(subject="x" * 201),                              # a subject over the cap
    _answer(action="forward"),                               # an unknown act
    _answer().replace('"kind": "new"', '"kind": "reply"'),   # another kind
    "APPROVE",                                               # a plain approve
])
def test_the_answer_refuses_each_other_shape(bad: str) -> None:
    """Mutation: drop a check of ``parse_new_mail_answer`` → red."""
    assert rcard.parse_new_mail_answer(bad) is None


def test_the_answer_of_a_reply_and_of_a_new_mail_never_cross() -> None:
    """Mutation: let ``parse_reply_answer`` take an optional ``kind`` → red."""
    reply = "APPROVE " + json.dumps({"reply": {
        "action": "send", "reply_all": False, "body": "Yes.", "to": [F.KNOWN], "cc": [],
        "message_id": F.MAIL}})
    assert rcard.parse_reply_answer(_answer()) is None
    assert rcard.parse_new_mail_answer(reply) is None


async def test_the_answer_is_recorded_by_respond_input(world) -> None:
    """Mutation: leave the new-mail answer out of ``_recordable`` → red."""
    assert await F.record_answer("c" * 32, _answer())


def test_the_form_of_a_new_mail_shows_whole_and_needs_a_to() -> None:
    """Mutation: clip the subject, or drop the To check → red."""
    people = {"to": [F.KNOWN], "cc": []}
    form = ask_tools.clean_reply_form({
        "kind": "new", "mailbox": "Work", "subject": "S" * 200, "body": TEXT,
        "recipients": {"reply": people, "reply_all": people}, "files": ["a.pdf (4 KB)"]})
    assert form["kind"] == "new" and form["subject"] == "S" * 200
    assert form["files"] == ["a.pdf (4 KB)"] and form["message_id"] == ""
    for bad in ({"subject": "Rates\nBcc: x@evil.example"}, {"subject": "S" * 201},
                {"recipients": {"reply_all": {"to": [], "cc": [F.KNOWN]}}}):
        with pytest.raises(ValueError):
            ask_tools.clean_reply_form({
                "kind": "new", "body": TEXT,
                "recipients": {"reply": people, "reply_all": people}, **bad})


def test_the_form_keeps_each_warning_whole_and_the_text_limit_of_its_act() -> None:
    """Fix round 1, P3. The note holds look-alike warnings past the four
    notices, and none is cut. A held form caps its text at ``HELD_TEXT_MAX``,
    as its act does. Mutation: clip the note at 500, or drop ``max_body`` →
    red."""
    from acb_skills.held_card import HELD_TEXT_MAX

    people = {"to": [F.KNOWN], "cc": []}
    warning = "x@contos0.test: the domain looks like contoso.test, which you know"
    note = " ".join([warning] * 20)
    base = {"kind": "new", "body": TEXT, "recipients": {"reply": people, "reply_all": people}}
    form = ask_tools.clean_reply_form({**base, "note": note, "max_body": HELD_TEXT_MAX})
    assert form["note"] == note and len(note) > 500
    assert form["max_body"] == HELD_TEXT_MAX
    assert ask_tools.clean_reply_form(base)["max_body"] == rcard.MAX_REPLY_BODY
    with pytest.raises(ValueError):
        ask_tools.clean_reply_form({**base, "body": "x" * (HELD_TEXT_MAX + 1),
                                    "max_body": HELD_TEXT_MAX})
    for bad in (0, True, rcard.MAX_REPLY_BODY + 1, "3000"):
        with pytest.raises(ValueError):
            ask_tools.clean_reply_form({**base, "max_body": bad})


def test_the_client_builds_the_new_mail_answer_that_the_server_reads() -> None:
    """``lib/replyCard.ts`` builds the answer as ``const mail = {...}``. Its
    keys must be the server's, or a Send from the card acts on nothing."""
    src = (REPO / "workbench/control_plane/src/lib/replyCard.ts").read_text(encoding="utf-8")
    block = re.search(r"const mail = \{(.*?)\};", src, re.S)
    assert block, "newMailAnswer builds its object as `const mail = {...}`"
    keys = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
    assert keys == set(rcard.NEW_MAIL_ANSWER_KEYS), keys
    # Fix round 1: the one optional key, only for a form with file rows.
    assert set(rcard.NEW_MAIL_ANSWER_OPTIONAL_KEYS) == {"files"}
    assert "...(form.fileRows ? { files } : {})" in block.group(1)
    assert f"MAX_SUBJECT = {rcard.MAX_SUBJECT}" in src


# ── 2. The act sends the answer, as the record allows ───────────────────────


async def test_the_act_sends_the_answered_subject_and_text(world) -> None:
    """Mutation: send ``rec["body"]`` or ``rec["subject"]`` → red."""
    card = await _card()
    assert card["body"] == TEXT and len(card["card_id"]) == 32
    out = await _act(card["card_id"], _answer(subject="Rates for May", body="Kim, see below."))
    [sent] = world["sends"].mails
    assert sent.subject == "Rates for May" and sent.body_text == "Kim, see below."
    assert sent.to == [F.KNOWN] and sent.cc == [F.COLLEAGUE] and sent.bcc is None
    assert out["action"] == "send" and out["edited"] is True
    assert out["subject"] == "Rates for May" and out["body"] == "Kim, see below."


async def test_the_act_sends_only_the_ticked_people(world) -> None:
    """Mutation: send the recorded lists whole → red."""
    card = await _card()
    out = await _act(card["card_id"], _answer(cc=()))
    [sent] = world["sends"].mails
    assert sent.to == [F.KNOWN] and sent.cc is None
    assert out["left_out"] == 1 and out["edited"] is False


async def test_the_act_keeps_the_files_as_recorded(world) -> None:
    """The answer names no file, so the files are the record's. Mutation:
    take the files from the request → red."""
    card = await _card(artifacts=[{"path": "outputs/rates.pdf"}])
    assert card["files"] == ['File: "outputs/rates.pdf"']
    await _act(card["card_id"], _answer())
    [sent] = world["sends"].mails
    assert [a.path for a in sent.artifacts] == ["outputs/rates.pdf"]


async def test_save_draft_writes_a_new_draft_and_sends_nothing(world) -> None:
    """Mutation: send on Save, or save the recorded text → red."""
    card = await _card()
    out = await _act(card["card_id"], _answer(action="save", body="Draft text."))
    assert world["sends"].mails == []
    [saved] = world["saves"]
    assert saved.draft_id is None and saved.body == "Draft text."
    assert saved.to == [F.KNOWN] and saved.from_chat is True
    assert out == {**out, "action": "save", "draft_id": "draft-new-1"}


# ── 3. Each refusal sends and saves nothing ─────────────────────────────────


@pytest.mark.parametrize("people", [
    {"to": (F.STRANGER,)},                       # a To that the record does not hold
    {"cc": (F.COLLEAGUE, F.STRANGER)},           # an added Cc
    {"bcc": (F.STRANGER,)},                      # an added Bcc
    {"cc": (F.COLLEAGUE, F.COLLEAGUE)},          # a person named twice
])
async def test_a_person_not_on_the_record_sends_nothing(world, people: dict) -> None:
    """Mutation: take the people from the answer → red."""
    card = await _card()
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], _answer(**people))
    assert e.value.status_code == 409
    assert world["sends"].mails == [] and world["saves"] == []


@pytest.mark.parametrize("body", [BIDI, TAGS])
async def test_hidden_text_in_the_answered_text_sends_nothing(world, body: str) -> None:
    """Mutation: clean the text in silence in place of the refusal → red."""
    card = await _card()
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], _answer(body=body))
    assert e.value.status_code == 422 and "hidden characters" in e.value.detail
    assert world["sends"].mails == []


@pytest.mark.parametrize("subject", [
    "Rates\nBcc: x@evil.example",                # a header in a subject
    "Rates\rBcc: x@evil.example",                # a carriage return
    "Rates" + chr(0x2028) + "Bcc: x",            # a line separator
    "Rates" + chr(0x2029) + "Bcc: x",            # a paragraph separator
    "Rates" + chr(0x200B) + "now",               # a zero-width space
    "Rates " + chr(0x202E) + "won",              # a bidi control
])
async def test_a_bad_subject_sends_nothing(world, subject: str) -> None:
    """Mutation: skip ``_clean_subject`` in the act, or drop U+2028 from
    ``SUBJECT_LINE_BREAKS`` → red."""
    card = await _card()
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], _answer(subject=subject))
    assert e.value.status_code == 422
    assert world["sends"].mails == []


@pytest.mark.parametrize("subject", [
    chr(0x26A0) + chr(0xFE0F) + " Invoice due",              # a sign with VS16
    chr(0x2764) + chr(0xFE0F),                               # a heart with VS16
    "Thanks " + chr(0x1F44D) + chr(0x1F3FD),                 # a skin tone
    chr(0x1F468) + chr(0x200D) + chr(0x1F469) + chr(0x200D) + chr(0x1F467) + " Family",  # a ZWJ run
])
async def test_an_emoji_subject_goes_whole(world, subject: str) -> None:
    """Fix round 1: a subject takes the text's character rule, so an emoji
    keeps its selector and its joiner. Mutation: go back to
    ``is_invisible_char`` for each character → red."""
    card = await _card(subject=subject)
    assert card["subject"] == subject
    await _act(card["card_id"], _answer(subject=subject))
    [sent] = world["sends"].mails
    assert sent.subject == subject


async def test_a_second_answer_sends_nothing(world) -> None:
    """Mutation: read the record with GET in place of GETDEL → red."""
    card = await _card()
    answer = _answer()
    await _act(card["card_id"], answer)
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], answer)
    assert e.value.status_code == 409
    assert len(world["sends"].mails) == 1


@pytest.mark.parametrize("how", ["no_record", "other_member", "other_thread", "other_text"])
async def test_an_answer_that_was_not_posted_sends_nothing(world, how: str) -> None:
    """Mutation: drop ``take_reply_answer`` → red."""
    card = await _card()
    answer = _answer()
    if how == "no_record":
        req = nm.NewMailActRequest(card_id=card["card_id"], card_thread_id="t", answer=answer)
        call = nm.new_mail_card_act(req, BackgroundTasks(), F.user())
    else:
        await F.record_answer(card["card_id"], answer,
                              who=F.OTHER if how == "other_member" else F.MEMBER,
                              thread="other" if how == "other_thread" else "chat-thread-1")
        posted = _answer(body="Another text.") if how == "other_text" else answer
        req = nm.NewMailActRequest(card_id=card["card_id"], card_thread_id="chat-thread-1",
                                   answer=posted)
        call = nm.new_mail_card_act(req, BackgroundTasks(), F.user())
    with pytest.raises(HTTPException) as e:
        await call
    assert e.value.status_code == 409
    assert world["sends"].mails == []


async def test_a_held_card_id_is_no_new_mail_card(world) -> None:
    """The two records live apart. Mutation: one namespace → red."""
    held = await hc.held_card(hc.HeldCardRequest(
        kind="new", account_id=F.BOX, to=[F.KNOWN], subject="Rates", note=TEXT), F.user())
    with pytest.raises(HTTPException) as e:
        await _act(held["card_id"], _answer(cc=()))
    assert e.value.status_code == 409
    assert world["sends"].mails == []


# ── R7 fences of fix round 1 ─────────────────────────────────────────────────

PAGE_REF = "knowledge:0a0b0c0d-0000-4000-8000-0000000000aa"


def _metorite(monkeypatch, digest: str = "d" * 64) -> list[str]:
    """Stand in for ``resolve_metorite_files``: one page of 2 KB, as the
    member sees it. It records each set of refs that it resolved."""
    import types

    from gateway.routes.email.transport import metorite_files as mf

    calls: list[str] = []

    async def resolve(user: Any, refs: list[str], **_k: Any) -> list[Any]:
        calls.extend(refs)
        return [types.SimpleNamespace(ref=r, source="knowledge", name="Price list.md",
                                      size=2048, mime="text/markdown", content=b"# Prices",
                                      digest=digest) for r in refs]

    monkeypatch.setattr(mf, "resolve_metorite_files", resolve)
    return calls


async def test_the_act_sends_the_recorded_metorite_file_with_its_digest(
    world, monkeypatch,
) -> None:
    """The card resolves each Metorite file as the member and records its
    digest, and the send carries it, so a file that changed sends nothing
    (H-392). Mutation: drop ``metorite_files=`` from the send → red."""
    calls = _metorite(monkeypatch)
    card = await _card(metorite_files=[PAGE_REF])
    assert card["files"] == ['File: "Price list.md" (2 KB)'] and calls == [PAGE_REF]
    await _act(card["card_id"], _answer())
    [sent] = world["sends"].mails
    assert [(m.ref, m.digest) for m in sent.metorite_files] == [(PAGE_REF, "d" * 64)]


async def test_save_draft_attaches_the_recorded_metorite_file(world, monkeypatch) -> None:
    """Save draft resolves the file again and keeps it only with the same
    digest. Mutation: drop the Metorite attachments from the save → red."""
    _metorite(monkeypatch)
    card = await _card(metorite_files=[PAGE_REF])
    await _act(card["card_id"], _answer(action="save"))
    [saved] = world["saves"]
    assert [a.filename for a in saved.attachments] == ["Price list.md"]
    assert world["sends"].mails == []


@pytest.mark.parametrize("fails_on", [1, 2])
async def test_a_redis_fault_in_the_act_sends_nothing(world, monkeypatch, fails_on) -> None:
    """A fault of the read of the answer (1) or of the card record (2)
    refuses the act. Mutation: treat a fault as a match → red."""
    card = await _card()
    answer = _answer()
    await F.record_answer(card["card_id"], answer)
    fake = world["redis"]
    real = fake.getdel
    seen = {"n": 0}

    async def getdel(k: str) -> Any:
        seen["n"] += 1
        if seen["n"] == fails_on:
            raise ConnectionError("redis is down")
        return await real(k)

    monkeypatch.setattr(fake, "getdel", getdel)
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], answer, record=False)
    assert e.value.status_code == 409
    assert world["sends"].mails == [] and world["saves"] == []


async def test_the_act_checks_the_mailbox_again(world, monkeypatch) -> None:
    """The member lost the mailbox after the card: the act sends nothing.
    Mutation: drop the owner check from the act → red."""
    from gateway.routes.email import core

    card = await _card()

    async def gone(*_a: Any, **_k: Any) -> None:
        raise HTTPException(status_code=404, detail="Account not found")

    monkeypatch.setattr(core, "_assert_account_owner", gone)
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], _answer())
    assert e.value.status_code == 404
    assert world["sends"].mails == [] and world["saves"] == []


async def test_a_mixed_case_session_email_reaches_the_mailbox_check_as_it_is(
    world, monkeypatch,
) -> None:
    """Fix round 1: ``mailbox_predicate`` compares ``user_id`` with case, so
    the card and the act ask the owner check with the session's own email.
    Only the digest folds the case. Mutation: lower the email for the
    mailbox check → red."""
    from gateway.routes.email import core

    who = "Jane@Acme.test"
    asked: list[str] = []

    async def owner(_db: Any, account_id: str, member: str, **_k: Any) -> None:
        asked.append(member)

    monkeypatch.setattr(core, "_assert_account_owner", owner)
    req = nm.NewMailCardRequest(account_id=F.BOX, to=[F.KNOWN], subject="Rates", body=TEXT)
    card = await nm.new_mail_card(req, F.user(who))
    answer = _answer(cc=())
    await F.record_answer(card["card_id"], answer, who=who)
    act = nm.NewMailActRequest(card_id=card["card_id"], card_thread_id="chat-thread-1",
                               answer=answer)
    await nm.new_mail_card_act(act, BackgroundTasks(), F.user(who))
    assert asked == [who, who]
    assert len(world["sends"].mails) == 1


async def test_the_card_shows_no_hidden_character(world) -> None:
    card = await _card(body="Hi" + chr(0x200B) + " Kim")
    assert card["body"] == "Hi Kim"


async def test_the_card_and_the_act_take_no_extra_field() -> None:
    """Mutation: drop ``extra="forbid"`` → red."""
    for extra in ({"classes": {}}, {"card_id": "a" * 32}, {"files": ["x"]}):
        with pytest.raises(ValidationError):
            nm.NewMailCardRequest(account_id=F.BOX, to=[F.KNOWN], **extra)
    for extra in ({"to": [F.STRANGER]}, {"body": "x"}, {"subject": "x"}, {"files": []}):
        with pytest.raises(ValidationError):
            nm.NewMailActRequest(card_id="a" * 32, card_thread_id="t", answer="x", **extra)


# ── 5. The held path keeps the rules of §21 on the form ─────────────────────


async def _held(**kw: Any) -> dict[str, Any]:
    body = {"account_id": F.BOX, "to": [F.KNOWN, F.STRANGER], "subject": "Rates",
            "note": TEXT, **kw}
    return await hc.held_card(hc.HeldCardRequest(kind="new", **body), F.user())


async def _held_act(card_id: str, answer: str) -> dict[str, Any]:
    await F.record_answer(card_id, answer)
    req = hc.HeldCardActRequest(card_id=card_id, card_thread_id="chat-thread-1", answer=answer)
    return await hc.held_card_act(req, BackgroundTasks(), F.user())


async def test_held_card_gives_the_form_with_a_new_address_unticked(world) -> None:
    """Mutation: start the form from the people in place of the rows → red."""
    card = await _held(files=[f"attachment:{F.FILE_PDF}"])
    form = card["form"]
    assert form["recipients"]["reply_all"]["to"] == [F.KNOWN, F.STRANGER]
    assert form["ticked"]["reply_all"]["to"] == [F.KNOWN]
    assert form["classes"][F.STRANGER] == "new" and form["classes"][F.KNOWN] == "sent"
    # Fix round 1 (§21.1 item 3): each file is a row with its tick and hint.
    assert form["file_rows"] == [{"id": "file-0", "label": 'File: "quote.pdf" (4 KB)',
                                  "hint": 'From the mail "Quote" of your own mailbox.',
                                  "checked": True}]
    clean = ask_tools.clean_reply_form(form)
    assert clean["ticked"]["reply_all"]["to"] == [F.KNOWN]
    assert clean["file_rows"] == form["file_rows"]
    assert card["detail"].endswith("Only a ticked row goes. 1 new address starts unticked.")
    assert "2 people, 1 file." in card["detail"]
    # Fix round 1, P3: the held act refuses a text over HELD_TEXT_MAX, so the
    # card caps its box there.
    from acb_skills.held_card import HELD_TEXT_MAX

    assert form["max_body"] == HELD_TEXT_MAX == clean["max_body"]


async def test_held_form_answer_sends_the_answered_text_and_the_recorded_files(world) -> None:
    """Mutation: send ``rec["body"]`` in ``_act_new_form`` → red."""
    card = await _held(files=[f"attachment:{F.FILE_PDF}"])
    out = await _held_act(card["card_id"],
                          _answer(body="The new rates.", cc=(), files=["file-0"]))
    [sent] = world["sends"].mails
    assert sent.to == [F.KNOWN] and sent.body_text == "The new rates."
    assert world["sends"].fetched == [F.FILE_PDF]
    assert out["edited"] is True and out["left_out"] == 1


@pytest.mark.parametrize("files", [[], None])
async def test_held_form_answer_sends_no_unticked_file(world, files) -> None:
    """Fix round 1 (§21.1 item 3): the member unticked the file, or the
    answer names none. The mail goes without it. Mutation: send each file
    row whatever the answer names → red."""
    card = await _held(files=[f"attachment:{F.FILE_PDF}"])
    extra = {} if files is None else {"files": files}
    out = await _held_act(card["card_id"], _answer(cc=(), **extra))
    [sent] = world["sends"].mails
    assert world["sends"].fetched == [] and not sent.attachments
    assert out["files"] == 0 and out["left_out"] == 2


async def test_held_form_answer_with_a_forged_file_sends_nothing(world) -> None:
    """A file id that the record does not hold refuses the whole answer."""
    card = await _held(files=[f"attachment:{F.FILE_PDF}"])
    with pytest.raises(HTTPException) as e:
        await _held_act(card["card_id"], _answer(cc=(), files=["file-9"]))
    assert e.value.status_code == 409
    assert world["sends"].mails == [] and world["sends"].fetched == []


async def test_a_plain_card_answer_that_names_files_sends_nothing(world) -> None:
    """The plain card's files are fixed lines with no tick, so its answer
    names no ``files``. Mutation: ignore the key → red."""
    card = await _card()
    with pytest.raises(HTTPException) as e:
        await _act(card["card_id"], _answer(files=[]))
    assert e.value.status_code == 422
    assert world["sends"].mails == []


async def test_held_form_save_writes_a_new_draft_and_sends_nothing(world) -> None:
    card = await _held()
    out = await _held_act(card["card_id"], _answer(action="save", cc=()))
    assert world["sends"].mails == [] and out["action"] == "save"
    [saved] = world["saves"]
    assert saved.to == [F.KNOWN] and saved.body == TEXT


@pytest.mark.parametrize(("answer", "status"), [
    (_answer(to=(F.LOOKALIKE,), cc=()), 409),                # a person not on the card
    (_answer(cc=(), body=LOOKALIKE_WORD), 422),              # a look-alike word
    (_answer(cc=(), subject="P" + chr(0x0430) + "y now"), 422),
    (_answer(cc=(), body="x " * 1600), 422),                 # over HELD_TEXT_MAX
    (_answer(cc=(), body=BIDI), 422),                        # hidden text
])
async def test_held_form_answer_refusals_send_nothing(world, answer: str, status: int) -> None:
    """Mutation: drop ``refuse_lookalike`` or ``_clean_text`` in the act → red."""
    card = await _held()
    with pytest.raises(HTTPException) as e:
        await _held_act(card["card_id"], answer)
    assert e.value.status_code == status
    assert world["sends"].mails == [] and world["saves"] == []


async def test_held_form_answer_with_no_to_sends_nothing(world) -> None:
    """The stranger is the only ticked person: it is a Cc of nothing, and
    the answer has no To of the record. Mutation: drop the To check → red."""
    card = await _held(to=[F.KNOWN], cc=[F.STRANGER])
    with pytest.raises(HTTPException) as e:
        await _held_act(card["card_id"], _answer(to=(), cc=(F.STRANGER,)))
    assert e.value.status_code == 409
    assert world["sends"].mails == []


# ── 5a. Dark: EMAIL_EDIT_CARD_ORGS (fix round 1) ─────────────────────────────


@pytest.mark.parametrize(("value", "org", "on"), [
    ("", F.ORG, False),                           # empty: off, the default
    ("*", F.ORG, True),                           # each org
    (f"other-org, {F.ORG}", F.ORG, True),         # a list that names the org
    ("other-org", F.ORG, False),                  # a list that does not
    (F.ORG, "", False),                           # no tenant under a list
])
def test_the_flag_reads_its_list_and_fails_closed(monkeypatch, value, org, on) -> None:
    """Mutation: read an empty list as each org → red."""
    edit_card(monkeypatch, value)
    assert rcard.edit_card_on(org) is on


@pytest.mark.parametrize(("flag", "channel", "whatsapp", "open_"), [
    ("", True, False, False),     # flag off: the cards of before
    ("*", True, False, True),     # flag on, a live chat
    ("*", True, True, False),     # a WhatsApp run keeps the card of before
    ("*", False, False, False),   # no card channel keeps it too
])
def test_the_edit_card_needs_the_flag_a_channel_and_no_whatsapp(
    monkeypatch, flag, channel, whatsapp, open_,
) -> None:
    """``ask_tools.edit_card_channel_open``. Mutation: drop any of its three
    tests → red."""
    edit_card(monkeypatch, flag)
    monkeypatch.setattr(ask_tools, "confirmation_channel_open", lambda: channel)
    monkeypatch.setattr(ask_tools, "_whatsapp_run", lambda: whatsapp)
    assert ask_tools.edit_card_channel_open() is open_


async def test_the_new_card_routes_are_dark_while_the_flag_is_off(world, monkeypatch) -> None:
    """Off, each route answers 404 and records nothing. Mutation: drop
    ``_off`` from either route → red."""
    edit_card(monkeypatch, "")
    with pytest.raises(HTTPException) as e:
        await _card()
    assert e.value.status_code == 404 and world["redis"].keys == {}
    req = nm.NewMailActRequest(card_id="c" * 32, card_thread_id="t", answer=_answer())
    with pytest.raises(HTTPException) as e:
        await nm.new_mail_card_act(req, BackgroundTasks(), F.user())
    assert e.value.status_code == 404 and world["sends"].mails == []


async def test_held_card_with_the_flag_off_is_exactly_the_rows_card(world, monkeypatch) -> None:
    """Off, the held card gives no form, and its act takes no form answer.
    Mutation: add the form with the flag off → red."""
    edit_card(monkeypatch, "")
    card = await _held()
    assert "form" not in card and card["rows"]
    with pytest.raises(HTTPException) as e:
        await _held_act(card["card_id"], _answer(cc=()))
    assert e.value.status_code == 409
    assert world["sends"].mails == []


# ── 6. send_email draws the card and acts on the exact answer ───────────────


agents = F.load_email_agent("ea_new_mail_card")


class _Gw:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.act: dict[str, Any] = {
            "ok": True, "action": "send", "id": "m-9", "to": [F.KNOWN], "cc": [],
            "subject": "Rates for May", "body": "Edited by Jane.", "edited": True,
            "left_out": 1, "files": 0}

    async def post(self, path: str, body: dict[str, Any] | None = None, **_k: Any) -> Any:
        self.posts.append((path, dict(body or {})))
        if path == "/email/new-card":
            return {"card_id": "f" * 32, "mailbox": f"Work · {F.MEMBER}", "subject": "Rates",
                    "body": TEXT, "to": [F.KNOWN], "cc": [F.COLLEAGUE], "bcc": [],
                    "files": []}
        if path in ("/email/new-card/act", "/email/held-card/act"):
            return self.act
        if path == "/email/send":
            return {"id": "old-1"}
        if path == "/email/held-card":
            return {"card_id": "e" * 32, "title": "Send this new email?", "detail": "d",
                    "context": "c", "rows": [{"id": "to-0", "label": f"To: {F.KNOWN}"}],
                    "form": {"kind": "new", "mailbox": "Work", "subject": "Rates",
                             "body": TEXT,
                             "recipients": {"reply": {"to": [F.KNOWN, F.STRANGER], "cc": []},
                                            "reply_all": {"to": [F.KNOWN, F.STRANGER],
                                                          "cc": []}},
                             "ticked": {"reply": {"to": [F.KNOWN], "cc": []},
                                        "reply_all": {"to": [F.KNOWN], "cc": []}},
                             "classes": {F.KNOWN: "sent", F.STRANGER: "new"},
                             "files": [], "notices": [], "note": ""}}
        raise AssertionError(f"unexpected POST {path}")


@pytest.fixture()
def gw(monkeypatch) -> _Gw:
    g = _Gw()
    monkeypatch.setattr(agents, "_post", g.post)
    monkeypatch.setattr(agents, "_current_user_email", lambda: F.MEMBER)
    monkeypatch.setattr(agents, "_egress_held", lambda: False)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: False)

    async def name(_aid: str) -> str:
        return f"Work · {F.MEMBER}"

    async def no_note(*_a: Any) -> str:
        return ""

    async def no_rule(_aid: str, to: list[str], cc: list[str], _bcc: Any) -> Any:
        return cc, []

    monkeypatch.setattr(agents, "_mailbox_name", name)
    monkeypatch.setattr(agents, "_usual_mailbox_note", no_note)
    monkeypatch.setattr(agents, "_always_copy_cc", no_rule)
    monkeypatch.setattr("acb_skills.ask_tools.confirmation_channel_open", lambda: True)
    return g


def _cards(monkeypatch, answer: str | None) -> list[dict[str, Any]]:
    """Stand in for the card. *answer* is what the member's browser posts,
    or None for a plain Approve of an old card."""
    seen: list[dict[str, Any]] = []

    async def card(**kw: Any) -> Any:
        seen.append(kw)
        if answer is None:
            return True
        return ask_tools.ReplyAnswer(kw.get("request_id") or "r", answer,
                                     rcard.parse_new_mail_answer(answer), F.MEMBER, "th-1")

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    return seen


async def test_send_email_draws_the_card_and_posts_the_exact_answer(monkeypatch, gw) -> None:
    """Mutation: post the tool's own body to the act, or skip the card → red."""
    monkeypatch.setattr(agents, "_edit_card_open", lambda: True)
    answer = _answer(subject="Rates for May", body="Edited by Jane.", cc=())
    cards = _cards(monkeypatch, answer)
    out = await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], cc=[F.COLLEAGUE],
                                  subject="Rates")
    assert [p for p, _b in gw.posts] == ["/email/new-card", "/email/new-card/act"]
    # The card records the mailbox, the people, the subject and the text of the call.
    assert gw.posts[0][1] == {"account_id": F.BOX, "to": [F.KNOWN], "cc": [F.COLLEAGUE],
                              "bcc": [], "subject": "Rates", "body": TEXT, "artifacts": [],
                              "metorite_files": []}
    assert gw.posts[1][1] == {"card_id": "f" * 32, "card_thread_id": "th-1", "answer": answer}
    [card] = cards
    assert card["reply"]["kind"] == "new" and card["reply"]["body"] == TEXT
    assert card["request_id"] == "f" * 32
    assert "changed the email on the card" in out and "Edited by Jane." in out
    assert "Rates for May" in out and "unticked 1 person" in out


async def test_send_email_says_nothing_went_on_a_discard(monkeypatch, gw) -> None:
    monkeypatch.setattr(agents, "_edit_card_open", lambda: True)

    async def card(**_kw: Any) -> Any:
        return None

    monkeypatch.setattr("acb_skills.ask_tools.request_confirmation", card)
    out = await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Rates")
    assert "not sent" in out
    assert [p for p, _b in gw.posts] == ["/email/new-card"]


async def test_send_email_with_no_edit_card_keeps_the_old_card(monkeypatch, gw) -> None:
    """A WhatsApp run draws no card to edit. Mutation: always draw it → red."""
    monkeypatch.setattr(agents, "_edit_card_open", lambda: False)
    cards = _cards(monkeypatch, None)
    await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], subject="Rates")
    assert [p for p, _b in gw.posts] == ["/email/send"]
    assert "reply" not in cards[0]


@pytest.mark.parametrize(("flag", "posts"), [
    ("", ["/email/send"]),                                  # off: the plain card
    ("*", ["/email/new-card", "/email/new-card/act"]),      # on: the edit card
])
async def test_send_email_follows_the_flag(monkeypatch, gw, flag, posts) -> None:
    """The real ``_edit_card_open``, in a live chat. Off, the plain card of
    before, exactly. Mutation: drop ``edit_card_on`` from
    ``edit_card_channel_open`` → red."""
    edit_card(monkeypatch, flag)
    monkeypatch.setattr(ask_tools, "_whatsapp_run", lambda: False)
    cards = _cards(monkeypatch, _answer(cc=()) if flag else None)
    await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN], cc=[F.COLLEAGUE], subject="Rates")
    assert [p for p, _b in gw.posts] == posts
    assert ("reply" in cards[0]) is bool(flag)


@pytest.mark.parametrize(("flag", "drawn"), [("", "rows"), ("*", "reply")])
async def test_a_held_new_mail_follows_the_flag(monkeypatch, gw, flag, drawn) -> None:
    """A held chat: off, the rows card of §21. On, the held form."""
    edit_card(monkeypatch, flag)
    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    monkeypatch.setattr(ask_tools, "_whatsapp_run", lambda: False)
    monkeypatch.setattr("acb_skills.ask_tools.reply_card_channel_open", lambda: True)
    cards = _cards(monkeypatch, _answer(cc=()) if flag else None)
    await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN, F.STRANGER], subject="Rates")
    assert drawn in cards[0] and ("reply" if drawn == "rows" else "rows") not in cards[0]


async def test_send_email_of_a_held_chat_draws_the_held_form(monkeypatch, gw) -> None:
    """The held card's own record and act, drawn as the one card. Mutation:
    draw the rows card where the form can draw → red."""
    monkeypatch.setattr(agents, "_held_card_mode", lambda: True)
    monkeypatch.setattr(agents, "_egress_held", lambda: True)
    monkeypatch.setattr(agents, "_edit_card_open", lambda: True)
    monkeypatch.setattr("acb_skills.ask_tools.reply_card_channel_open", lambda: True)
    answer = _answer(cc=(), body="Edited by Jane.")
    cards = _cards(monkeypatch, answer)
    out = await agents.send_email(F.BOX, body=TEXT, to=[F.KNOWN, F.STRANGER], subject="Rates")
    assert [p for p, _b in gw.posts] == ["/email/held-card", "/email/held-card/act"]
    assert gw.posts[1][1]["answer"] == answer and gw.posts[1][1]["card_id"] == "e" * 32
    [card] = cards
    assert "rows" not in card and card["reply"]["ticked"]["reply_all"]["to"] == [F.KNOWN]
    assert any("starts unticked" in n for n in card["reply"]["notices"])
    assert "Edited by Jane." in out
