"""The reply card of the chat: two routes (EM-CR, ``email_app_master_plan.md`` §18).

The email assistant's ``compose_reply`` calls them. The member confirms ONE
reply on ONE card in the chat, and the reply then completes. No Email app
opens.

1. ``POST /email/reply-card`` drafts the reply with the ONE drafter
   (``draft_reply_smart``, with ``create_draft`` off) and derives the
   recipients for Reply and for Reply all from the original mail. The card
   shows what it answers. It also writes the text of the card to the
   member's Drafts, so the member can finish it in the Email app (owner,
   2026-10-22). It updates the chat's own unedited draft, and it never
   writes over a draft of the member. Nothing is sent.
2. ``POST /email/reply-card/act`` does what the member chose: send, or save
   to Drafts. It takes NO recipient and NO body of its own. The body comes
   from the card answer that the member's browser posted to
   ``/agent/respond-input``, and the route sends it only when that route's
   record of the answer matches (``acb_skills.reply_card``). It derives To
   and Cc again from the original mail and the Reply all choice of the
   member, and it refuses when they differ from what the card showed.

3. ``POST /email/reply-card/discard`` is the member's own Discard, which the
   browser posts. It trashes the one draft that the card wrote, and only
   while its text is still the text that the card wrote.

So in a chat that read outside mail (H-236 holds that run), the model can
still get the member's reply out, and it cannot choose where it goes or what
it says. Fence: ``tests/unit/test_email_chat_reply.py``.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from email.utils import parseaddr
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_skills.reply_card import MAX_REPLY_BODY, clean_reply_body
from fastapi import BackgroundTasks, Depends, HTTPException
from gateway.routes.email.automation.drafting import (
    DraftReplyRequest,
    _store_ai_draft,
    _upsert_reply_draft,
    chat_draft_record,
    draft_reply_smart,
    strip_placeholder_lines,
    trash_chat_draft,
)
from gateway.routes.email.automation.identity import resolve_self
from gateway.routes.email.core import (
    _log,
    _tenant_session,
    provider_session,
    router,
)
from gateway.routes.email.quoting import strip_for_reading
from gateway.routes.email.transport.send import (
    SendEmailRequest,
    _as_uuid,
    send_email,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

#: The longest excerpt of the original mail on the card.
EXCERPT_CHARS = 600

#: The mail a reply card answers: a mail of a mailbox of the caller. The
#: owner join keeps a mail of another member out, as ``_reply_target`` does.
REPLY_ROW_SQL = (
    "SELECT em.id::text AS id, em.account_id::text AS account_id, "
    "em.provider_message_id, em.thread_id, em.subject, em.from_address, "
    "em.to_addresses, em.cc_addresses, em.received_at, em.snippet, em.body_text, "
    "em.folder, em.internet_message_id, ea.provider, "
    "(SELECT s.signature FROM email_assistant_settings s "
    " WHERE s.account_id = em.account_id) AS signature "
    "FROM email_messages em JOIN email_accounts ea ON ea.id = em.account_id "
    "WHERE em.id = CAST(:mid AS uuid) AND ea.user_id = :uid"
)

#: The 404 of a mail that is not the caller's.
REPLY_CARD_NOT_FOUND = "Message not found"


class ReplyCardRequest(BaseModel):
    """The mail to answer. No recipient: the server derives it (EM-CR).

    ``draft`` is the text that the member and the assistant agreed in the
    chat. It only fills the card, which shows it in full for the member to
    edit. The send still takes the body that the MEMBER submitted, so the
    text of ``draft`` never leaves on its own (``ReplyCardActRequest``)."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    draft: str = Field(default="", max_length=MAX_REPLY_BODY)
    #: With no ``draft``: the member's steer for the drafter ("say yes to
    #: Friday"). The card still shows the result in full.
    instruction: str = Field(default="", max_length=2000)
    #: The Reply all choice that the card opens with. The draft in Drafts
    #: takes the recipients of that choice. The server still derives them.
    reply_all: bool = False


class ReplyCardDiscardRequest(BaseModel):
    """The member's own Discard on a reply card: the mail, and the draft that
    THIS card wrote (``draft_id`` of ``/email/reply-card``). Nothing else."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    draft_id: str = Field(max_length=64)


class ReplyCardActRequest(BaseModel):
    """What the member chose, as the card answer that the browser posted.

    ``card_thread_id`` is the thread that owns the card, which keys the record
    that respond-input wrote (review of #1191).

    ``extra="forbid"``: a ``to``, a ``cc``, a ``bcc``, a ``body`` or an
    ``attachments`` field is a 422, so no caller can add one. Fence:
    ``tests/unit/test_email_chat_reply.py``."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    card_request_id: str
    card_thread_id: str
    answer: str


def _address(item: Any) -> str:
    """The address of one stored recipient (``{name, email}``) or string."""
    if isinstance(item, dict):
        raw = str(item.get("email") or "")
    else:
        raw = str(item or "")
    addr = parseaddr(raw)[1] or raw
    addr = addr.strip()
    return addr if "@" in addr else ""


def _addresses(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except ValueError:
            value = []
    if isinstance(value, dict):
        value = [value]
    return [a for a in (_address(v) for v in (value or [])) if a]


def _unique(addrs: Iterable[str], skip: set[str]) -> list[str]:
    """*addrs* in order, each once, less each address in *skip*. The
    compare is lower case, and *skip* grows with each kept address."""
    out: list[str] = []
    for a in addrs:
        low = a.strip().lower()
        if low and low not in skip:
            skip.add(low)
            out.append(a.strip())
    return out


def derive_reply_recipients(
    *,
    sender: str,
    reply_to: Sequence[str],
    to: Sequence[str],
    cc: Sequence[str],
    own: Iterable[str],
    reply_all: bool,
) -> tuple[list[str], list[str]]:
    """To and Cc of a reply. THE one rule, and only the server runs it.

    * Reply: the Reply-To of the mail when it names one, else the sender.
      When those are all the member's own addresses (the member wrote the
      mail), the reply goes to the To of the mail instead, as a mail app does.
    * Reply all: Reply's To, then each To of the mail. Cc is each Cc of the
      mail. Each list leaves out the member's own addresses (each mailbox of
      the member, D-EM-27) and each address that came before.

    No Bcc, ever. A mail with no one to answer gives ``([], [])``.
    """
    mine = {a.strip().lower() for a in own if a and a.strip()}
    primary = [a for a in (reply_to or []) if a] or ([sender] if sender else [])
    first = _unique(primary, set(mine))
    if not first:
        first = _unique(to, set(mine))
    if not reply_all:
        return first, []
    seen = set(mine)
    to_out = _unique([*first, *to], seen)
    cc_out = _unique(cc, seen)
    return to_out, cc_out


def _mailbox_text(label: str, address: str) -> str:
    return f"{label} · {address}" if label and address and label != address else address


def _reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    return s if s.lower().startswith("re:") else f"Re: {s}".strip()


async def _reply_row(db: Any, message_id: str, owner: str) -> Any:
    mid = _as_uuid(message_id)
    if mid is None:
        raise HTTPException(status_code=404, detail=REPLY_CARD_NOT_FOUND)
    row = (await db.execute(text(REPLY_ROW_SQL), {"mid": mid, "uid": owner})).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=REPLY_CARD_NOT_FOUND)
    return row


async def _read_reply_to(db: Any, owner: str, row: Any) -> list[str]:
    """The Reply-To of the mail, from its provider. A fault names none, so
    the reply goes to the sender, as before this card existed. The act route
    reads it again, and refuses when the answer changed.

    The stored folder and Message-ID go with the read: an IMAP UID is unique
    in one folder only, so the provider checks that the message it read is
    this one (review of #1191). IMAP keeps the Message-ID as the thread id."""
    try:
        async with provider_session(
            db, owner, message_id=str(row.id), require_auth=False,
        ) as sess:
            if not sess.authed:
                return []
            return list(await sess.provider.get_reply_to(
                sess.provider_message_id, folder=getattr(row, "folder", None),
                message_id=(getattr(row, "internet_message_id", None)
                            or getattr(row, "thread_id", None))))
    except Exception as exc:  # noqa: BLE001 - the class only
        _log.warning("email.reply_card_reply_to_failed", error=type(exc).__name__)
        return []


class _Mail:
    """The parts of the original mail that a reply card needs."""

    def __init__(self, row: Any, me: Any, reply_to: list[str]) -> None:
        frm = row.from_address if isinstance(row.from_address, dict) else (
            json.loads(row.from_address or "{}") if isinstance(row.from_address, str) else {})
        self.account_id = str(row.account_id)
        self.provider_message_id = row.provider_message_id
        self.thread_id = row.thread_id or None
        self.subject = row.subject or ""
        self.sender = _address(frm)
        self.sender_name = str((frm or {}).get("name") or "").strip()
        self.to = _addresses(row.to_addresses)
        self.cc = _addresses(row.cc_addresses)
        self.reply_to = reply_to
        self.own = set(me.self_addresses) | ({me.address.lower()} if me.address else set())
        self.mailbox = _mailbox_text(me.label, me.address)
        self.address = me.address
        self.received = row.received_at.isoformat() if getattr(
            row.received_at, "isoformat", None) else str(row.received_at or "")
        body = strip_for_reading(row.body_text or "") or row.snippet or ""
        self.excerpt = " ".join(body.split())[:EXCERPT_CHARS]
        self.provider = str(getattr(row, "provider", "") or "")
        self.signature = str(getattr(row, "signature", "") or "")

    def notices(self) -> list[str]:
        """What the card must say and never hide (review of #1191, P2 and P1d):
        a Reply that goes to another address than the sender, and the text
        that the send adds after the card."""
        out = self.reply_to_notices()
        if self.signature.strip():
            out.append("When it goes, your mailbox signature is added below the "
                       "text if the text does not hold it.")
        if self.provider == "microsoft":
            out.append("Outlook adds the email you answer below your reply.")
        return out

    def reply_to_notices(self) -> list[str]:
        """The line that says a Reply goes to another address than the sender.
        A Reply-To can send a reply to a stranger, so each card that shows
        such a reply says it (review of #1191, P2)."""
        sender = self.sender.lower()
        if self.reply_to and sender and [a.lower() for a in self.reply_to] != [sender]:
            return [f"Reply goes to {', '.join(self.reply_to)}, not to the sender "
                    f"{self.sender}."]
        return []

    def recipients(self, reply_all: bool) -> tuple[list[str], list[str]]:
        return derive_reply_recipients(
            sender=self.sender, reply_to=self.reply_to, to=self.to, cc=self.cc,
            own=self.own, reply_all=reply_all)


async def _load_mail(message_id: str, owner: str) -> _Mail:
    async with _tenant_session() as db:
        row = await _reply_row(db, message_id, owner)
        me = await resolve_self(db, str(row.account_id))
        reply_to = await _read_reply_to(db, owner, row)
    return _Mail(row, me, reply_to)


async def reply_recipients(
    message_id: str, owner: str, *, reply_all: bool = False,
) -> tuple[list[str], list[str]]:
    """To and Cc of a reply to *message_id*, by :func:`derive_reply_recipients`.

    Each path that addresses a reply uses it: the reply card, a reply draft
    that ``/email/draft-reply`` or ``/email/drafts/save`` saves, and a reply
    that ``send_email`` sends. Before, those took the sender alone, so a
    reply ignored Reply-To and a nudge on a mail the member sent went to the
    member."""
    to, cc, _notices = await reply_recipients_with_notices(
        message_id, owner, reply_all=reply_all)
    return to, cc


async def reply_recipients_with_notices(
    message_id: str, owner: str, *, reply_all: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    """:func:`reply_recipients` and the Reply-To line that a card must show."""
    mail = await _load_mail(message_id, owner)
    to, cc = mail.recipients(reply_all)
    return to, cc, mail.reply_to_notices()


@router.get("/reply-recipients")
async def reply_recipients_route(
    message_id: str,
    reply_all: bool = False,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Who a reply to this mail goes to, and the Reply-To line that the send
    card must show (review round 1, P2-a). A read: it writes nothing."""
    to, cc, notices = await reply_recipients_with_notices(
        message_id, user.email or "anonymous", reply_all=reply_all)
    return {"to": to, "cc": cc, "notices": notices}


@router.post("/reply-card")
async def reply_card(
    req: ReplyCardRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """What a reply card shows: the mail, the draft, and both recipient sets.

    The draft comes from ``/email/draft-reply`` with ``create_draft`` off.
    Each placeholder-only line leaves the draft. A mail with no one to
    answer is a 422.

    The text of the card then goes to the member's Drafts, as the one draft
    of the conversation (owner, 2026-10-22). A new card for the same mail
    updates that draft, so each revision in the chat leaves one draft. The
    member can finish and send it in the Email app. Nothing is sent. The
    write is best effort: a failure leaves the card as it was."""
    owner = user.email or "anonymous"
    mail = await _load_mail(req.message_id, owner)
    reply_to, reply_cc = mail.recipients(False)
    all_to, all_cc = mail.recipients(True)
    if not reply_to:
        raise HTTPException(status_code=422, detail="This mail has no one to reply to.")
    # The chat agreed a text with the member: the card shows that text, and
    # the drafter does not write a second, different one.
    agreed = clean_reply_body(strip_placeholder_lines(req.draft))
    drafted: dict[str, Any] | None = None
    # The text that Metorite wrote BEFORE this card. The drafter below
    # replaces the record, and the save must still know the chat's own draft.
    prior = await _chat_record(mail)
    if not agreed.strip():
        drafted = await draft_reply_smart(
            DraftReplyRequest(account_id=mail.account_id, message_id=req.message_id,
                              create_draft=False, instruction=req.instruction),
            user,
        )
    # Review of #1191, P1: no hidden character and no long blank run reaches
    # the card, so the card shows each character that the send can carry.
    body = agreed if agreed.strip() else clean_reply_body(
        strip_placeholder_lines(str((drafted or {}).get("draft") or "")))
    draft_id, kept_draft = "", False
    if body.strip():
        to, cc = (all_to, all_cc) if req.reply_all else (reply_to, reply_cc)
        draft_id, kept_draft = await _keep_card_draft(
            mail, owner, to, cc, _reply_subject(mail.subject), body, prior=prior)
    out: dict[str, Any] = {
        "message_id": str(_as_uuid(req.message_id) or req.message_id),
        # A file the drafter did not read is named, so the member checks the
        # reply against it (``draft_files``).
        "notices": [*mail.notices(), *[str(n) for n in (drafted or {}).get("notices") or []]],
        "account_id": mail.account_id,
        "mailbox": mail.mailbox,
        "subject": _reply_subject(mail.subject),
        "original": {
            "from": (f"{mail.sender_name} <{mail.sender}>" if mail.sender_name
                     else mail.sender),
            "subject": mail.subject,
            "received": mail.received,
            "excerpt": mail.excerpt,
        },
        "recipients": {
            "reply": {"to": reply_to, "cc": reply_cc},
            "reply_all": {"to": all_to, "cc": all_cc},
        },
        "body": body,
        "skipped": (drafted or {}).get("skipped"),
        "draft_id": draft_id,
        # A draft of the member stays in Drafts, beside the card's draft.
        "kept_draft": kept_draft,
    }
    from acb_skills.held_card import held_card_send_enabled

    if held_card_send_enabled():
        # §21: the server classes each person of both choices. The person
        # that a Reply answers is ``thread``, unless the member knows them.
        # Each other person that the member does not know is ``new``.
        out["classes"] = await _reply_classes(
            owner, [*reply_to, *reply_cc, *all_to, *all_cc], primary=reply_to)
    return out


async def _reply_classes(
    owner: str, addresses: list[str], *, primary: list[str],
) -> dict[str, str]:
    """The class of each address of a reply card (§21.3, the Reply row).

    Only the person that a Reply answers can be ``thread``. Open question 1
    took the safer choice: a person that only Reply all adds, and that the
    member does not know, is ``new``. A fault fails closed: each address is
    ``new``, so the card opens on Reply and marks each one."""
    from acb_skills.held_card import CLASS_NEW
    from gateway.routes.email.automation.held_card import classify_addresses

    try:
        async with _tenant_session() as db:
            return await classify_addresses(db, owner, addresses, thread_senders=primary)
    except Exception as exc:  # each address reads as new
        _log.warning("email.reply_card_classes_failed", error=type(exc).__name__)
        return {a.strip().lower(): CLASS_NEW for a in addresses if a and a.strip()}


def _same(a: Sequence[str], b: Sequence[str]) -> bool:
    return [x.strip().lower() for x in a] == [x.strip().lower() for x in b]


@router.post("/reply-card/act")
async def reply_card_act(
    req: ReplyCardActRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Send the member's reply, or save it to Drafts, exactly as confirmed.

    The answer must be on record for THIS card and THIS member
    (``reply_card.take_reply_answer``), and it is taken once. The To and Cc
    are derived again here, and they must equal the lists the card showed.
    Each refusal sends and saves nothing."""
    from acb_skills.reply_card import (
        parse_reply_answer,
        reply_body_refusal,
        take_reply_answer,
    )

    owner = (user.email or "").strip().lower()
    org = getattr(user, "organization_id", None)
    if not owner or not await take_reply_answer(
            org, req.card_request_id, owner, req.answer, req.card_thread_id):
        _log.warning("email.reply_card_answer_refused", reason="no_record")
        raise HTTPException(
            status_code=409,
            detail="This reply was not confirmed on its card, so nothing was sent or saved.",
        )
    choice = parse_reply_answer(req.answer)
    if choice is None:
        raise HTTPException(status_code=422, detail="The card answer is not a reply.")
    if _as_uuid(choice.message_id) is None or _as_uuid(choice.message_id) != _as_uuid(
            req.message_id):
        _log.warning("email.reply_card_answer_refused", reason="another_email")
        raise HTTPException(
            status_code=409,
            detail="This card answers another email, so nothing was sent or saved.")
    refusal = reply_body_refusal(choice.body)
    if refusal:
        _log.warning("email.reply_card_answer_refused", reason="hidden_text")
        raise HTTPException(status_code=422, detail=refusal)
    # The same cleaning as the draft of the card: what leaves is what was visible.
    body = clean_reply_body(choice.body)
    if not body:
        raise HTTPException(status_code=422, detail="The reply text is empty.")
    mail = await _load_mail(req.message_id, user.email or owner)
    to, cc = mail.recipients(choice.reply_all)
    if not to or not _same(to, choice.to) or not _same(cc, choice.cc):
        _log.warning("email.reply_card_answer_refused", reason="recipients_changed")
        raise HTTPException(
            status_code=409,
            detail=("The recipients of this mail changed after the card showed them, "
                    "so nothing was sent or saved. Ask for the reply again."),
        )
    subject = _reply_subject(mail.subject)
    if choice.action == "send":
        res = await send_email(
            SendEmailRequest(
                account_id=mail.account_id, to=to, cc=cc or None, subject=subject,
                body_text=body, reply_to_message_id=req.message_id,
            ),
            background, user,
        )
        _log.info("email.reply_card_sent", reply_all=choice.reply_all)
        return {"ok": True, "action": "send", "to": to, "cc": cc, "mailbox": mail.mailbox,
                "id": (res or {}).get("id")}
    draft_id = await _save_draft(mail, user.email or owner, to, cc, subject, body)
    _log.info("email.reply_card_saved", reply_all=choice.reply_all)
    return {"ok": True, "action": "save", "to": to, "cc": cc, "mailbox": mail.mailbox,
            "draft_id": draft_id}


async def _chat_record(mail: _Mail) -> list[str] | None:
    """The text that Metorite recorded for the thread, or None on a fault.
    None lets the save read the record itself."""
    try:
        async with _tenant_session() as db:
            return await chat_draft_record(db, mail.account_id, mail.thread_id)
    except Exception as exc:  # the save reads it again
        _log.warning("email.reply_card_record_failed", error=type(exc).__name__)
        return None


async def _write_draft(
    mail: _Mail, owner: str, to: list[str], cc: list[str], subject: str, body: str,
    *, chat_wrote: bool = False, prior: list[str] | None = None,
) -> tuple[str, bool]:
    """Write the reply draft of the conversation. Returns its LOCAL id, and
    True when a draft of the member stays beside it.

    :func:`drafting._upsert_reply_draft` updates the chat's own unedited
    draft, or makes one, and never writes over a draft of the member.
    ``exact_to`` writes the To over the one Outlook's ``createReply`` sets.

    ``chat_wrote`` records the body as the text that Metorite wrote
    (``email_ai_drafts``). A send of the reply, or the member's Discard, then
    trashes the draft only while it still holds that text."""
    async with _tenant_session() as db:
        async with provider_session(db, owner, account_id=mail.account_id) as sess:
            wrote = await _upsert_reply_draft(
                db, sess.provider, mail.account_id, thread_id=mail.thread_id,
                reply_to_provider_id=mail.provider_message_id,
                owner_email=mail.address, to=to, cc=cc, subject=subject,
                body=body, exact_to=True, prior=prior, exact_cc=not chat_wrote,
            )
        if chat_wrote and mail.thread_id:
            await _store_ai_draft(db, mail.account_id, mail.thread_id, body,
                                  commit=False)
    return wrote.local_id, wrote.kept_draft


async def _keep_card_draft(
    mail: _Mail, owner: str, to: list[str], cc: list[str], subject: str, body: str,
    *, prior: list[str] | None = None,
) -> tuple[str, bool]:
    """The draft of a card that shows: best effort, so the card always shows."""
    try:
        return await _write_draft(mail, owner, to, cc, subject, body,
                                  chat_wrote=True, prior=prior)
    except Exception as exc:  # the card still shows
        _log.warning("email.reply_card_draft_failed", error=type(exc).__name__)
        return "", False


async def _save_draft(
    mail: _Mail, owner: str, to: list[str], cc: list[str], subject: str, body: str,
) -> str:
    """Save the member's reply as the reply draft of the conversation.

    It returns the LOCAL id, which ``/email/drafts/schedule`` and
    ``send_draft`` take. It returned the provider id before, and a schedule
    of that id found no draft. The member chose the Cc on the card, so the
    save writes it as it is."""
    local_id, _kept = await _write_draft(mail, owner, to, cc, subject, body)
    return local_id


@router.post("/reply-card/discard")
async def reply_card_discard(
    req: ReplyCardDiscardRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The member pressed Discard on a reply card: trash the draft it wrote.

    The browser posts this on the member's own click. The model never calls
    it, so a card that times out keeps its draft. It trashes ONLY the draft
    ``draft_id``, and only while its stored and live bodies are still the
    text that Metorite wrote. A draft that the member edited stays, and so
    does each other draft of the thread. It sends nothing."""
    owner = user.email or "anonymous"
    mail = await _load_mail(req.message_id, owner)
    if not mail.thread_id:
        return {"ok": True, "trashed": False}
    async with (_tenant_session() as db,
                provider_session(db, owner, account_id=mail.account_id) as sess):
        gone = await trash_chat_draft(
            db, sess.provider, mail.account_id, mail.thread_id, req.draft_id)
    return {"ok": True, "trashed": gone}
