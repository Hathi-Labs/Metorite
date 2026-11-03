"""The card of a NEW mail in the chat: two routes (§18.10, owner 2026-11-03).

The email assistant's ``send_email`` calls them for a new mail in a chat
that shows a card. The member edits the subject and the text on ONE card,
ticks the people, and presses Send, Save draft or Discard. The card is the
reply card of §18.4 with ``kind: "new"``. No second card mechanism exists.

1. ``POST /email/new-card`` checks the mailbox, each address, the subject
   and the text. It records the lists, the files and the text under a card
   id that it mints, for the member, in tenant Redis. It sends nothing.
2. ``POST /email/new-card/act`` does what the member chose. It takes NO
   recipient, NO file and NO mailbox of its own. The answer must be the one
   that the member's browser posted to ``/agent/respond-input``
   (``reply_card.take_reply_answer``), and the act takes it once. The act
   takes the record once too. The people come from the record, and the
   member's ticks only take people off. The subject and the text are the
   member's, checked again. The files go as the record holds them.

A held chat (H-236) never reaches these routes. Its new mail goes through
``POST /email/held-card`` (§21), which keeps the classes and the unticked
new addresses. Fence: ``tests/unit/test_email_new_mail_card.py``.
"""
from __future__ import annotations

import base64
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_skills.reply_card import (
    MAX_NEW_MAIL_ANSWER,
    MAX_REPLY_BODY,
    MAX_REPLY_RECIPIENTS,
)
from fastapi import BackgroundTasks, Depends, HTTPException
from gateway.routes.email.automation.held_card import (
    _clean_subject,
    _org_of,
    _owner,
    _people,
    take_record,
    write_record,
)
from gateway.routes.email.automation.identity import resolve_self
from gateway.routes.email.automation.reply_card import ticked_subset
from gateway.routes.email.core import _log, _tenant_session, router
from gateway.routes.email.transport.send import ArtifactAttachment
from pydantic import BaseModel, ConfigDict, Field

#: The Redis namespace of a new-mail card (``cc:<org>:new-mail-card:<id>``).
#: It is not the namespace of a held card, so neither act takes the other's.
RECORD_NAMESPACE = "new-mail-card"
#: The most files of one card, of each kind.
MAX_CARD_FILES = 20
#: How long the act waits for the record of the answer
#: (``reply_card.TAKE_WAIT_S``). respond-input writes it just after the run
#: takes the answer.
ANSWER_WAIT_S = 3.0

#: The refusals of the act. Each one sends and saves nothing.
NOT_CONFIRMED = "This email was not confirmed on its card, so nothing was sent or saved."
NO_CARD = "The card of this email has expired or was used, so nothing was sent or saved."
NOT_AN_ANSWER = "The card answer is not a new email, so nothing was sent or saved."
PEOPLE_CHANGED = ("The card names a person that is not on this email, or no one in To, so "
                  "nothing was sent or saved. Ask for the email again.")
NO_TEXT = "The email has no text, so nothing was sent or saved."
TEXT_TOO_LONG = "The text is too long for one email, so nothing was sent or saved."


class NewMailCardRequest(BaseModel):
    """The new mail that the tool asks the card for. The server checks it.

    ``extra="forbid"``: any other field is a 422."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1, max_length=64)
    to: list[str] = Field(default_factory=list, max_length=MAX_REPLY_RECIPIENTS)
    cc: list[str] = Field(default_factory=list, max_length=MAX_REPLY_RECIPIENTS)
    bcc: list[str] = Field(default_factory=list, max_length=MAX_REPLY_RECIPIENTS)
    subject: str = Field(default="", max_length=1000)
    body: str = Field(default="", max_length=MAX_REPLY_BODY)
    #: Files of the member's own workspace of an agent, by path. The send
    #: resolves them, and a refused one fails the send (H-201 part 3).
    artifacts: list[ArtifactAttachment] = Field(default_factory=list, max_length=MAX_CARD_FILES)
    #: Metorite files that the member can see (H-392). This route resolves
    #: each one as the member, and records its digest.
    metorite_files: list[str] = Field(default_factory=list, max_length=MAX_CARD_FILES)


class NewMailActRequest(BaseModel):
    """The member's answer to a new-mail card, as the browser posted it.

    ``card_id`` is the id that ``POST /email/new-card`` minted, and the
    card's ``request_id``. ``card_thread_id`` keys the record of the answer.
    No recipient, no text and no file: a 422 refuses each extra field."""

    model_config = ConfigDict(extra="forbid")

    card_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    card_thread_id: str = Field(min_length=1, max_length=200)
    answer: str = Field(max_length=MAX_NEW_MAIL_ANSWER)


def _size_text(size: Any) -> str:
    try:
        n = int(size)
    except (TypeError, ValueError):
        return ""
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def checked_text(body: str) -> str:
    """The member's text as it leaves, or a 422 (§18.10 rule 2).

    THE check of an answered text of a new mail: hidden text is refused
    (``reply_card.reply_body_refusal``), never cleaned in silence. The text
    is then cleaned as the card showed it (``reply_card.clean_reply_body``).
    An empty text and a text over ``MAX_REPLY_BODY`` are refused."""
    from acb_skills.reply_card import clean_reply_body, reply_body_refusal

    refusal = reply_body_refusal(body or "")
    if refusal:
        raise HTTPException(status_code=422, detail=refusal)
    shown = clean_reply_body(body or "")
    if not shown:
        raise HTTPException(status_code=422, detail=NO_TEXT)
    if len(shown) > MAX_REPLY_BODY:
        raise HTTPException(status_code=422, detail=TEXT_TOO_LONG)
    return shown


def _mailbox_text(me: Any) -> str:
    label, address = str(getattr(me, "label", "") or ""), str(getattr(me, "address", "") or "")
    return f"{label} · {address}" if label and address and label != address else address


@router.post("/new-card")
async def new_mail_card(
    req: NewMailCardRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Check and record the card of one new mail. Nothing is sent.

    The answer is what the card shows: ``card_id``, the sending mailbox, the
    people, the subject, the text and a line for each file. The text is
    cleaned of each hidden character, so the card shows each character that
    a send can carry. An address that the card cannot show, a subject with a
    line break or a hidden character, and a mailbox of another member are
    each refused, and no card shows."""
    from acb_skills.held_card import file_label
    from acb_skills.reply_card import clean_reply_body
    from gateway.routes.email.core import _assert_account_owner
    from gateway.routes.email.transport.metorite_files import resolve_metorite_files

    owner = _owner(user)
    lists = _people(req)
    body = clean_reply_body(req.body)
    if not body:
        raise HTTPException(status_code=422, detail="The email has no text, so nothing was sent.")
    subject = _clean_subject(req.subject)
    async with _tenant_session() as db:
        await _assert_account_owner(db, req.account_id, owner, level="send")
        me = await resolve_self(db, req.account_id)
    shared = (await resolve_metorite_files(user, list(req.metorite_files))
              if req.metorite_files else [])
    artifacts = [a.model_dump(exclude_none=True) for a in req.artifacts]
    record = {
        "v": 1, "owner": owner.lower(), "kind": "new-mail", "account_id": req.account_id,
        "to": lists["to"], "cc": lists["cc"], "bcc": lists["bcc"],
        "subject": subject, "body": body, "artifacts": artifacts,
        "metorite": [{"ref": f.ref, "digest": f.digest} for f in shared],
    }
    card_id = await write_record(user, record, namespace=RECORD_NAMESPACE)
    # The one label of a file row (``held_card.file_label``): the name in
    # quotes, so a name that a sender chose never reads as a person.
    files = ([file_label(str(a.get("name") or a["path"]), "") for a in artifacts]
             + [file_label(f.name, _size_text(f.size)) for f in shared])
    _log.info("email.new_mail_card_built", people=sum(len(v) for v in lists.values()),
              files=len(files))
    return {"card_id": card_id, "mailbox": _mailbox_text(me), "subject": subject,
            "body": body, "to": lists["to"], "cc": lists["cc"], "bcc": lists["bcc"],
            "files": files}


async def _metorite_attachments(user: UserContext, recorded: list[dict[str, str]]) -> list[Any]:
    """The bytes of each recorded Metorite file, for a saved draft. Each one
    resolves again as the member, and goes only while its digest is the
    digest that the card recorded (``metorite_files.check_digests``)."""
    from gateway.routes.email.transport.metorite_files import (
        check_digests,
        resolve_metorite_files,
    )
    from gateway.routes.email.transport.send import SendAttachment

    if not recorded:
        return []
    expect = {str(m["ref"]): str(m["digest"]) for m in recorded}
    files = await resolve_metorite_files(user, list(expect))
    check_digests(files, expect)
    return [SendAttachment(filename=f.name, mime_type=f.mime,
                           content_b64=base64.b64encode(f.content).decode("ascii"))
            for f in files]


@router.post("/new-card/act")
async def new_mail_card_act(
    req: NewMailActRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Send the member's new mail, or save it as a new draft, as answered.

    1. Take the answer once (``reply_card.take_reply_answer``). A miss, a
       second answer, another member, another thread or another text does
       nothing.
    2. Take the card record once. Only the member who asked has one.
    3. Take the people from the record. Each ticked person must be on it
       (``reply_card.ticked_subset``), and a To must be ticked.
    4. Check the answered text and subject again (§18.10 rules 2 and 3).
    5. Send, or save a NEW draft, with exactly that text, the ticked people
       and the recorded files."""
    from acb_skills.reply_card import parse_new_mail_answer, take_reply_answer
    from gateway.routes.email.automation.drafting import DraftUpsertRequest, upsert_draft
    from gateway.routes.email.core import _assert_account_owner
    from gateway.routes.email.transport.send import (
        MetoriteFileRef,
        SendEmailRequest,
        send_email,
    )

    owner = _owner(user).lower()
    org = _org_of(user)
    if not await take_reply_answer(org, req.card_id, owner, req.answer, req.card_thread_id,
                                   wait_s=ANSWER_WAIT_S):
        _log.warning("email.new_mail_card_refused", reason="no_answer")
        raise HTTPException(status_code=409, detail=NOT_CONFIRMED)
    rec = await take_record(user, req.card_id, namespace=RECORD_NAMESPACE)
    if rec is None or rec.get("kind") != "new-mail":
        _log.warning("email.new_mail_card_refused", reason="no_card")
        raise HTTPException(status_code=409, detail=NO_CARD)
    choice = parse_new_mail_answer(req.answer)
    if choice is None:
        _log.warning("email.new_mail_card_refused", reason="not_an_answer")
        raise HTTPException(status_code=422, detail=NOT_AN_ANSWER)
    to = ticked_subset(list(rec.get("to") or []), choice.to)
    cc = ticked_subset(list(rec.get("cc") or []), choice.cc)
    bcc = ticked_subset(list(rec.get("bcc") or []), choice.bcc)
    if not to or cc is None or bcc is None:
        _log.warning("email.new_mail_card_refused", reason="people")
        raise HTTPException(status_code=409, detail=PEOPLE_CHANGED)
    body = checked_text(choice.body)
    subject = _clean_subject(choice.subject)
    account_id = str(rec["account_id"])
    async with _tenant_session() as db:
        # The mailbox is still the member's to send from.
        await _assert_account_owner(db, account_id, owner, level="send")
    artifacts = [ArtifactAttachment(**a) for a in rec.get("artifacts") or []]
    metorite = [dict(m) for m in rec.get("metorite") or []]
    edited = subject != rec.get("subject") or body != rec.get("body")
    left_out = (len(rec.get("to") or []) + len(rec.get("cc") or []) + len(rec.get("bcc") or [])
                - len(to) - len(cc) - len(bcc))
    out: dict[str, Any] = {"ok": True, "to": to, "cc": cc, "subject": subject, "body": body,
                           "edited": edited, "left_out": left_out,
                           "files": len(artifacts) + len(metorite)}
    if bcc:
        out["bcc"] = bcc
    if choice.action == "send":
        res = await send_email(SendEmailRequest(
            account_id=account_id, to=to, cc=cc or None, bcc=bcc or None, subject=subject,
            body_text=body, artifacts=artifacts or None,
            metorite_files=[MetoriteFileRef(**m) for m in metorite] or None,
        ), background, user)
        # A person that the mail server refused got nothing (review P1 of §21.15).
        refused = sorted({str(a).lower() for a in (res or {}).get("refused") or []})
        for key in ("to", "cc", "bcc"):
            if key in out:
                out[key] = [a for a in out[key] if a.lower() not in set(refused)]
        _log.info("email.new_mail_card_sent", edited=edited, left_out=left_out,
                  refused=len(refused))
        return {**out, "action": "send", "id": (res or {}).get("id"),
                **({"refused": refused} if refused else {})}
    saved = await upsert_draft(DraftUpsertRequest(
        account_id=account_id, to=to, cc=cc, bcc=bcc, subject=subject, body=body,
        artifacts=artifacts, attachments=await _metorite_attachments(user, metorite),
        from_chat=True,
    ), user)
    _log.info("email.new_mail_card_saved", edited=edited, left_out=left_out)
    return {**out, "action": "save", "draft_id": str((saved or {}).get("id") or "")}
