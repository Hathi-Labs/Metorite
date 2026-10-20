"""The reply card of the chat: two routes (EM-CR, ``email_app_master_plan.md`` §18).

The email assistant's ``compose_reply`` calls them. The member confirms ONE
reply on ONE card in the chat, and the reply then completes. No Email app
opens.

1. ``POST /email/reply-card`` drafts the reply with the ONE drafter
   (``draft_reply_smart``, with ``create_draft`` off) and derives the
   recipients for Reply and for Reply all from the original mail. The card
   shows what it answers.
2. ``POST /email/reply-card/act`` does what the member chose: send, or save
   to Drafts. It takes NO recipient and NO body of its own. The body comes
   from the card answer that the member's browser posted to
   ``/agent/respond-input``, and the route sends it only when that route's
   record of the answer matches (``acb_skills.reply_card``). It derives To
   and Cc again from the original mail and the Reply all choice of the
   member, and it refuses when they differ from what the card showed.

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
    _upsert_local_draft,
    draft_reply_smart,
    strip_placeholder_lines,
)
from gateway.routes.email.automation.identity import resolve_self
from gateway.routes.email.core import (
    _log,
    _savepoint,
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
        out: list[str] = []
        sender = self.sender.lower()
        if self.reply_to and sender and [a.lower() for a in self.reply_to] != [sender]:
            out.append(f"Reply goes to {', '.join(self.reply_to)}, not to the sender "
                       f"{self.sender}.")
        if self.signature.strip():
            out.append("When it goes, your mailbox signature is added below the "
                       "text if the text does not hold it.")
        if self.provider == "microsoft":
            out.append("Outlook adds the email you answer below your reply.")
        return out

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


@router.post("/reply-card")
async def reply_card(
    req: ReplyCardRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """What a reply card shows: the mail, the draft, and both recipient sets.

    The draft comes from ``/email/draft-reply`` with ``create_draft`` off,
    so this writes no provider draft. Each placeholder-only line leaves the
    draft. A mail with no one to answer is a 422."""
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
    if not agreed.strip():
        drafted = await draft_reply_smart(
            DraftReplyRequest(account_id=mail.account_id, message_id=req.message_id,
                              create_draft=False),
            user,
        )
    # Review of #1191, P1: no hidden character and no long blank run reaches
    # the card, so the card shows each character that the send can carry.
    body = agreed if agreed.strip() else clean_reply_body(
        strip_placeholder_lines(str((drafted or {}).get("draft") or "")))
    return {
        "message_id": str(_as_uuid(req.message_id) or req.message_id),
        "notices": mail.notices(),
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
    }


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


async def _save_draft(
    mail: _Mail, owner: str, to: list[str], cc: list[str], subject: str, body: str,
) -> str:
    """A provider draft with exactly these recipients and this body, and its
    local mirror. ``exact_to`` writes the To over the one Outlook's
    ``createReply`` sets, so the draft holds what the card showed."""
    async with _tenant_session() as db:
        async with provider_session(db, owner, account_id=mail.account_id) as sess:
            provider_id = await sess.provider.create_draft(
                to=to, subject=subject, body_text=body,
                reply_to_message_id=mail.provider_message_id,
                thread_id=mail.thread_id, cc=cc or None, exact_to=True,
            )
        try:
            async with _savepoint(db):
                await _upsert_local_draft(
                    db, mail.account_id, provider_id, thread_id=mail.thread_id,
                    owner_email=mail.address, to_email=to[0], to_addresses=to,
                    cc=cc or None, subject=subject, body=body,
                )
        except Exception as exc:  # noqa: BLE001 - the provider holds the draft
            _log.warning("email.reply_card_mirror_failed", error=type(exc).__name__)
    return str(provider_id or "")
