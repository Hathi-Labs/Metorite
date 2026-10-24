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

#: The most people that one reply card adds to the people of the mail.
MAX_ADDED = 20
#: The Redis namespace of the record of a reply card's added people.
REPLY_RECORD_NAMESPACE = "reply-card"
#: The record lives a little longer than the card waits (``held_card``).
REPLY_RECORD_TTL_S = 3700

#: The notice of a card where the member was only on Bcc (item 11). A Reply
#: all would show the other people that the member got the mail.
BCC_ONLY_NOTICE = "You were Bcc'd, so only the sender is ticked."
#: The notice when the record of the added people failed: the card shows no
#: added person, because the act could not check one.
ADDED_NOT_RECORDED = ("The added people could not be kept on this card, so the draft in "
                      "Drafts was left as it was. Ask again in a moment.")
#: The classes that a Bcc of a held chat may have (§21.15, the rule of 21.10
#: open question 5): a person that the member knows. A held chat refuses a
#: Bcc to any other address, and no tick can allow it.
HELD_BCC_CLASSES = frozenset({"org", "sent"})
#: The refusal of a Bcc that a held chat may not send.
HELD_BCC_REFUSED = ("This chat read outside mail, so a Bcc can only go to a colleague or "
                    "to a person you wrote to before. {addr} is neither, so nothing was "
                    "shown or sent.")
#: The notice when a held chat leaves a Bcc of the thread's draft off the card.
HELD_DRAFT_BCC_NOTICE = ("The draft of this reply in Drafts has a Bcc to a new address. "
                         "This chat read outside mail, so the card leaves it out: {addrs}.")
#: The mark of a Bcc that the thread's draft holds (§21.15).
DRAFT_BCC_MARK = "on the draft in Drafts"

#: The colleagues that the member copies on mail of this thread, or on mail
#: to these domains: each Cc of the newest 500 Sent mails of the member's own
#: mailboxes, where the mail is of this thread, or goes To a person at one of
#: the domains. A Sent mail counts only when its From is the mailbox's own
#: address (§21 fix round 1, P1-a). The caller keeps an address with two
#: mails or more, or with one mail of this thread, and then only an ``org``
#: one. R8: ``tests/unit/test_email_recipient_intent_r8.py``.
USUAL_CC_SQL = """
WITH sent AS (
    SELECT em.id, em.thread_id, em.account_id, em.to_addresses, em.cc_addresses
      FROM email_messages em
      JOIN email_accounts ea ON ea.id = em.account_id AND ea.user_id = :uid
     WHERE LOWER(COALESCE(em.folder, '')) = 'sent'
       AND LOWER(em.from_address->>'email') = LOWER(ea.email_address)
     ORDER BY em.received_at DESC NULLS LAST
     LIMIT 500
), hit AS (
    SELECT s.id, (s.thread_id = CAST(:tid AS text)
                  AND s.account_id = CAST(:aid AS uuid)) AS in_thread, s.cc_addresses
      FROM sent s
     WHERE (s.thread_id = CAST(:tid AS text) AND s.account_id = CAST(:aid AS uuid))
        OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(COALESCE(s.to_addresses, '[]'::jsonb)) t
             WHERE SPLIT_PART(LOWER(t->>'email'), '@', 2) = ANY(CAST(:domains AS text[])))
)
SELECT LOWER(c->>'email') AS addr, COUNT(DISTINCT h.id) AS n,
       BOOL_OR(COALESCE(h.in_thread, false)) AS in_thread
  FROM hit h
  CROSS JOIN LATERAL jsonb_array_elements(COALESCE(h.cc_addresses, '[]'::jsonb)) AS c
 WHERE c->>'email' IS NOT NULL
 GROUP BY 1
"""

#: The newest reply draft of the thread (``drafting._THREAD_DRAFT_SQL``): the
#: people that ``edit_draft`` put on it go on the next card of the thread.
THREAD_DRAFT_PEOPLE_SQL = (
    "SELECT cc_addresses, bcc_addresses FROM email_messages"
    " WHERE account_id = CAST(:aid AS uuid) AND thread_id = :tid"
    " AND LOWER(COALESCE(folder, '')) IN ('drafts', 'draft')"
    " AND has_attachments IS NOT TRUE"
    " AND LOWER(COALESCE(subject, '')) NOT LIKE 'fw%'"
    " ORDER BY received_at DESC NULLS LAST, id DESC LIMIT 1")


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
    #: ``None`` lets the server choose (§22.6): Reply all when anyone besides
    #: the sender and the member is on the mail, else Reply.
    reply_all: bool | None = None
    #: Tick boxes (owner, 2026-10-24). True in a held chat with
    #: ``EMAIL_HELD_CARD_SEND`` on: each address that the server classes
    #: ``new`` starts unticked, and the draft in Drafts leaves it out, so the
    #: draft holds what the card sends. It only takes people OFF the card.
    untick_new: bool = False
    #: People that the member asked to copy (owner, 2026-10-24, item 2). The
    #: server classes each one with THE classifier and records the list, so
    #: the act takes only a subset of what this card showed. One card
    #: replaces the reply card, the edit_draft card and the send_draft card.
    cc: list[str] = Field(default_factory=list, max_length=MAX_ADDED)
    #: Bcc people that the tool proposes (§21.15, owner 2026-10-24). Classed
    #: as ``cc`` is, and recorded apart. The act takes a subset only.
    bcc: list[str] = Field(default_factory=list, max_length=MAX_ADDED)
    #: True when the chat read outside mail (H-236). The tool sets it from the
    #: run, never from the model. A held card refuses a Bcc that is not
    #: ``org`` or ``sent``, and the record keeps the flag for the Bcc that
    #: the member types on the card (``/reply-card/bcc``).
    held: bool = False


class ReplyCardBccRequest(BaseModel):
    """A Bcc that the member typed on the reply card (§21.15). The member's
    browser posts it, and no tool calls it. The server classes the address
    and adds it to the record of the card, so the act still takes a subset
    of the record only."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    card_request_id: str = Field(max_length=64)
    address: str = Field(max_length=320)


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


def auto_reply_all(reply_to: Sequence[str], reply_cc: Sequence[str],
                   all_to: Sequence[str], all_cc: Sequence[str]) -> bool:
    """The default choice of a card (§22.6, owner 2026-10-23): Reply all when
    Reply all adds anyone besides the sender and the member. Else Reply."""
    first = {a.strip().lower() for a in [*reply_to, *reply_cc] if a}
    return any(a.strip().lower() not in first for a in [*all_to, *all_cc] if a)


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
        self.folder = str(getattr(row, "folder", "") or "").lower()

    def was_received(self) -> bool:
        """True for a mail that the member got, not one the member wrote."""
        return self.folder not in ("sent", "drafts", "draft") and (
            self.sender.lower() not in self.own)

    def bcc_only(self) -> bool:
        """True when the member got this mail on Bcc only: no own address is
        a To or a Cc of it (item 11, owner 2026-10-24). A Reply all would
        then show the other people that the member got it."""
        if not self.was_received():
            return False
        return not any(a.lower() in self.own for a in [*self.to, *self.cc])

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
    reply_all = (req.reply_all if req.reply_all is not None
                 else auto_reply_all(reply_to, reply_cc, all_to, all_cc))
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
    from acb_skills.held_card import held_card_send_enabled

    asked = _asked_cc(req.cc)
    asked_bcc = _asked_cc(req.bcc, label="Bcc")
    own = {a.lower() for a in getattr(mail, "own", set()) or set()}
    on_mail = {a.strip().lower() for a in [*all_to, *all_cc, *reply_to, *reply_cc]}
    # The people of the thread's draft that ``edit_draft`` added stay on the
    # card (the owner's report of 2026-10-24, item 10): a new card used to
    # derive from the mail alone, so its Send dropped them.
    carried, draft_bcc = await _draft_people(mail)
    # Final check P1: item 9 writes the people of the mail into the draft. A
    # person of the mail, or the member, is never "carried". Only a genuinely
    # extra address of the draft is.
    carried = [a for a in carried if a.strip().lower() not in on_mail | own]
    usual = await _usual_candidates(owner, mail, [*all_to, *all_cc], skip=on_mail | own)
    # §21.16: the member's own "always copy" rules of this mailbox. Each
    # colleague is an org or a sent person NOW, or it is not offered.
    rule = await _rule_candidates(owner, mail, [mail.sender, *all_to, *all_cc],
                                  skip=on_mail | own)
    rule_match = {a.lower(): m for a, m in rule}
    candidates = _unique([*asked, *carried, *(a for a, _m in rule), *usual],
                         set(on_mail | own))[:MAX_ADDED]
    # §21.15: the Bcc of the card. A person on To or Cc is not a Bcc too.
    bcc_people = _unique([*asked_bcc, *draft_bcc],
                         {*on_mail, *own, *(a.lower() for a in candidates)})[:MAX_ADDED]
    held_on = held_card_send_enabled()
    classes: dict[str, str] | None = None
    if held_on or candidates or bcc_people:
        # §21 and item 1 (owner, 2026-10-24): the server classes each person.
        # The sender of a received mail of the thread, and each To and Cc of
        # one, is ``thread``, unless the domain is a look-alike. A Reply-To
        # that is not the sender is classed on its own merits (fix round 1,
        # P2-a), because a phishing mail names a stranger there.
        classes = await _reply_classes(
            owner, [*reply_to, *reply_cc, *all_to, *all_cc, *candidates, *bcc_people],
            primary=[mail.sender] if mail.sender else [],
            people=_mail_people(mail), account_id=mail.account_id,
            thread_id=mail.thread_id)
    # A carried address that a trusted thread mail names is a thread person:
    # it stays on the card and on the draft (a later check found that taking
    # it out of ``carried`` dropped it from both), it may start ticked, and
    # its mark says where it comes from rather than "added".
    carried_extra = [a for a in carried if (classes or {}).get(a.lower()) != "thread"]
    # Item 7: only a colleague of the organization is pre-added from Sent.
    usual_added = [a for a in usual if (classes or {}).get(a.lower()) == "org"]
    rule_added = [a for a in candidates if a.lower() in rule_match
                  and (classes or {}).get(a.lower()) in ("org", "sent")]
    added = [a for a in candidates
             if a in asked or a in carried or a in usual_added or a in rule_added]
    notices: list[str] = []
    bcc_people = _held_bcc(bcc_people, asked_bcc, classes, req.held, notices)
    # The record of the card: the added people, the Bcc and the held flag.
    # It is written for each card, so the member can type a Bcc on it.
    card_id = await _write_reply_record(
        user, str(_as_uuid(req.message_id)), added, bcc=bcc_people, held=req.held)
    record_failed = False
    if not card_id and (added or bcc_people):
        # Review P3-a: the card adds nobody, and the draft stays as it
        # was below, so a Cc that edit_draft put there is not lost.
        added, usual_added, carried, bcc_people = [], [], [], []
        record_failed = True
        notices.append(ADDED_NOT_RECORDED)
    # A Bcc that the tool proposed starts ticked. A Bcc of the draft starts
    # ticked only for a person that the member knows (review P2-a).
    bcc_ticked = [a for a in bcc_people if a in asked_bcc
                  or (classes or {}).get(a.lower()) in ("org", "sent", "thread")]
    bcc_only = bool(getattr(mail, "bcc_only", lambda: False)())
    # Item 11 (owner, 2026-10-24): ONE list, every person of the mail and
    # each added person, and each starts ticked. Only the sender starts
    # ticked when the member was on Bcc only, or when the tool asked for the
    # sender alone (``reply_all`` false). In a held chat with the switch on,
    # a ``new`` person starts unticked.
    everyone_to, everyone_cc = list(all_to), [*all_cc, *added]
    if bcc_only or req.reply_all is False:
        keep = {a.lower() for a in [*reply_to, *added]}
    else:
        keep = {a.lower() for a in [*everyone_to, *everyone_cc]}
    if req.untick_new and classes is not None:
        keep = {a for a in keep if classes.get(a, "new") != "new"}
    # Review P2-a: a held run's edit_draft can put a stranger on the draft.
    # A carried person starts unticked unless ``org`` or ``sent``, in any chat.
    keep -= {a.lower() for a in carried
             if (classes or {}).get(a.lower()) not in ("org", "sent", "thread")}
    ticked = {
        "reply": {"to": [a for a in reply_to if a.lower() in keep],
                  "cc": [a for a in added if a.lower() in keep]},
        "reply_all": {"to": [a for a in everyone_to if a.lower() in keep],
                      "cc": [a for a in everyone_cc if a.lower() in keep]},
    }
    if bcc_people:
        for choice in ("reply", "reply_all"):
            ticked[choice]["bcc"] = list(bcc_ticked)
    if bcc_only:
        notices.append(BCC_ONLY_NOTICE)
    if usual_added:
        notices.append(_usual_note(usual_added, mail))
    draft_id, kept_draft = "", False
    # Item 9: the draft in Drafts names exactly the people that start ticked.
    start = ticked["reply_all"]
    if body.strip() and start["to"] and not record_failed:
        draft_id, kept_draft = await _keep_card_draft(
            mail, owner, start["to"], start["cc"], _reply_subject(mail.subject), body,
            prior=prior, **({"bcc": start.get("bcc", [])} if bcc_people else {}))
    marks = _person_marks(mail, classes, asked, carried_extra, usual_added,
                          [*everyone_to, *everyone_cc], held=req.untick_new)
    marks.update(_bcc_marks(bcc_people, asked_bcc, classes, held=req.held))
    # §21.16: a colleague of the member's own rule says so, whatever else.
    from acb_skills.held_card import rule_copy_mark

    marks.update({a.lower(): rule_copy_mark(rule_match[a.lower()]) for a in rule_added
                  if a in added})
    out: dict[str, Any] = {
        "message_id": str(_as_uuid(req.message_id) or req.message_id),
        # A file the drafter did not read is named, so the member checks the
        # reply against it (``draft_files``).
        "notices": [*mail.notices(), *notices,
                    *[str(n) for n in (drafted or {}).get("notices") or []]],
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
            # "reply" is the shortcut "Only the sender" of the card. It is a
            # subset of "reply_all", so it never names a hidden person.
            "reply": {"to": reply_to, "cc": added,
                      **({"bcc": bcc_people} if bcc_people else {})},
            "reply_all": {"to": everyone_to, "cc": everyone_cc,
                          **({"bcc": bcc_people} if bcc_people else {})},
        },
        # The card shows the one list (item 11). The field stays for an
        # older client, and it names the choice that the start ticks match.
        "reply_all": reply_all and not bcc_only,
        "ticked": ticked,
        # The words of each person on the card (item 8), by lower case.
        "marks": marks,
        # The id of the record of the added people. The tool passes it as the
        # card's ``request_id``, so the act reads the same record.
        "card_id": card_id,
        # Item 9: the people that the draft in Drafts names.
        "draft_recipients": start if draft_id else {"to": [], "cc": []},
        # Item 6: a reply of this thread that went out in the last day.
        "recent_reply": await _recent_reply(mail),
        "body": body,
        "skipped": (drafted or {}).get("skipped"),
        "draft_id": draft_id,
        # A draft of the member stays in Drafts, beside the card's draft.
        "kept_draft": kept_draft,
    }
    if classes is not None:
        out["classes"] = classes
    return out


async def _reply_classes(
    owner: str, addresses: list[str], *, primary: list[str],
    people: Sequence[str] = (), account_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, str]:
    """The class of each address of a reply card (§21.3, the Reply row).

    The From sender of the mail is ``thread`` (*primary*), never a Reply-To
    that differs from it (fix round 1, P2-a). The owner answered open
    question 1 on 2026-10-24, "everyone on the original email": each To and
    Cc of the mail being answered is ``thread`` (*people*). A To or Cc of
    another mail of the thread is ``thread`` only when that mail's sender is
    ``org`` or ``sent`` (review P1-a). An address on a look-alike domain of
    ANY sender of the thread, or of an organization domain, stays ``new``
    (review P2-b). A fault fails closed: each address is ``new``."""
    from acb_skills.held_card import CLASS_NEW
    from gateway.routes.email.automation.held_card import (
        classify_addresses,
        org_domains,
        thread_senders,
        trusted_thread_participants,
    )

    try:
        async with _tenant_session() as db:
            everyone = list(people)
            senders: list[str] = []
            if account_id and thread_id:
                everyone += await trusted_thread_participants(
                    db, owner, account_id, thread_id)
                senders = await thread_senders(db, account_id, thread_id)
            doms = await org_domains(db, owner) if everyone else frozenset()
            return await classify_addresses(
                db, owner, addresses, thread_senders=primary, thread_people=everyone,
                org_domains=doms, guard_senders=senders)
    except Exception as exc:  # each address reads as new
        _log.warning("email.reply_card_classes_failed", error=type(exc).__name__)
        return {a.strip().lower(): CLASS_NEW for a in addresses if a and a.strip()}


#: The newest mail of the thread that the mailbox itself sent in the last 24
#: hours (item 6). A request to add people then forwards THAT mail, and the
#: reply does not go to the first people a second time.
RECENT_REPLY_SQL = (
    "SELECT em.id::text AS id, em.received_at FROM email_messages em"
    " JOIN email_accounts ea ON ea.id = em.account_id"
    " WHERE em.account_id = CAST(:aid AS uuid) AND em.thread_id = :tid"
    " AND LOWER(COALESCE(em.folder, '')) = 'sent'"
    " AND LOWER(em.from_address->>'email') = LOWER(ea.email_address)"
    " AND em.received_at > now() - interval '24 hours'"
    " ORDER BY em.received_at DESC LIMIT 1")


async def recent_thread_reply(db: Any, account_id: str, thread_id: str | None) -> dict[str, str]:
    """``{message_id, sent_at}`` of :data:`RECENT_REPLY_SQL`, or ``{}``."""
    if not thread_id:
        return {}
    row = (await db.execute(text(RECENT_REPLY_SQL),
                            {"aid": account_id, "tid": thread_id})).fetchone()
    if row is None:
        return {}
    when = row.received_at.isoformat() if getattr(row.received_at, "isoformat", None) else ""
    return {"message_id": str(row.id), "sent_at": when}


@router.get("/recent-reply")
async def recent_reply_route(
    draft_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """The recent reply of the thread of one draft of the member (item 6),
    for the send card of ``send_draft``. A read: it writes nothing."""
    did = _as_uuid(draft_id)
    if did is None:
        return {}
    async with _tenant_session() as db:
        row = (await db.execute(text(
            "SELECT em.account_id::text AS account_id, em.thread_id FROM email_messages em"
            " JOIN email_accounts ea ON ea.id = em.account_id"
            " WHERE em.id = CAST(:id AS uuid) AND ea.user_id = :uid"), {
            "id": did, "uid": user.email or "anonymous"})).fetchone()
        if row is None:
            return {}
        return await recent_thread_reply(db, row.account_id, row.thread_id)


async def _recent_reply(mail: Any) -> dict[str, str]:
    """The recent reply of the mail's thread. A fault reads as none."""
    try:
        async with _tenant_session() as db:
            return await recent_thread_reply(db, mail.account_id,
                                             getattr(mail, "thread_id", None))
    except Exception as exc:
        _log.warning("email.reply_card_recent_failed", error=type(exc).__name__)
        return {}


def _mail_people(mail: Any) -> list[str]:
    """Each To and Cc of the mail, when the member received it (item 1)."""
    received = getattr(mail, "was_received", None)
    if not callable(received) or not received():
        return []
    return [*getattr(mail, "to", []), *getattr(mail, "cc", [])]


def _asked_cc(cc: Sequence[str], *, label: str = "Cc") -> list[str]:
    """The people that the member asked to copy, each a whole address. An
    address that a card cannot show as it is answers 422, never cleaned."""
    from acb_skills.held_card import address_problem

    out: list[str] = []
    for a in cc:
        addr = parseaddr(str(a or ""))[1] or str(a or "")
        why = address_problem(addr.strip())
        if why:
            raise HTTPException(status_code=422, detail=f"A {label} address {why}.")
        out.append(addr.strip())
    return out


def _held_bcc(bcc: list[str], asked: Sequence[str], classes: dict[str, str] | None,
              held: bool, notices: list[str]) -> list[str]:
    """The Bcc of a card (§21.15). Outside a held chat, each one stays.

    In a held chat a Bcc goes only to an ``org`` or a ``sent`` person (21.10,
    open question 5). A Bcc that the tool proposed to anyone else answers
    422, so no card shows. A Bcc of the thread's draft to anyone else leaves
    the card, and a notice names it. A fault reads each person as new."""
    if not held:
        return bcc
    cls = classes or {}
    keep = [a for a in bcc if cls.get(a.lower(), "new") in HELD_BCC_CLASSES]
    asked_l = {a.lower() for a in asked}
    refused = [a for a in bcc if a not in keep and a.lower() in asked_l]
    if refused:
        raise HTTPException(status_code=422, detail=HELD_BCC_REFUSED.format(addr=refused[0]))
    dropped = [a for a in bcc if a not in keep]
    if dropped:
        notices.append(HELD_DRAFT_BCC_NOTICE.format(addrs=", ".join(dropped)))
    return keep


def _bcc_marks(bcc: Sequence[str], asked: Sequence[str], classes: dict[str, str] | None,
               *, held: bool) -> dict[str, str]:
    """The words of each Bcc on the card, by lower case."""
    from acb_skills.held_card import ADDED_MARK, ASSISTANT_ADDED_MARK, NEW_MARK

    asked_l = {a.lower() for a in asked}
    out: dict[str, str] = {}
    for a in bcc:
        low = a.lower()
        words = (ASSISTANT_ADDED_MARK if held else ADDED_MARK) if low in asked_l else DRAFT_BCC_MARK
        if (classes or {}).get(low) == "new":
            words = f"{NEW_MARK} · {words}"
        out[low] = words
    return out


async def _draft_people(mail: Any) -> tuple[list[str], list[str]]:
    """The Cc and the Bcc of the thread's newest reply draft, or none.

    ``edit_draft`` puts a Cc on that draft. A later card of the thread shows
    it as an added person, so its Send carries it (item 10). A fault reads
    as none."""
    thread_id = getattr(mail, "thread_id", None)
    if not thread_id:
        return [], []
    try:
        async with _tenant_session() as db:
            row = (await db.execute(text(THREAD_DRAFT_PEOPLE_SQL), {
                "aid": mail.account_id, "tid": thread_id})).fetchone()
    except Exception as exc:  # the card shows the people of the mail
        _log.warning("email.reply_card_draft_people_failed", error=type(exc).__name__)
        return [], []
    if row is None:
        return [], []
    return _addresses(row.cc_addresses), _addresses(row.bcc_addresses)


#: The fewest Sent mails with the same Cc that make the colleague usual.
USUAL_MIN = 2


async def _usual_candidates(
    owner: str, mail: Any, people: Sequence[str], *, skip: set[str],
) -> list[str]:
    """The people that the member copies on mail of this thread, or on mail
    to the domains of *people* (item 7a): two Sent mails or more, or one of
    this thread. The caller keeps only an ``org`` one. A fault reads as none."""
    from gateway.routes.email.automation.held_card import org_domains

    try:
        async with _tenant_session() as db:
            mine = await org_domains(db, owner)
            domains = sorted({a.rpartition("@")[2].lower() for a in people if "@" in a}
                             - set(mine))
            rows = (await db.execute(text(USUAL_CC_SQL), {
                "uid": owner, "tid": getattr(mail, "thread_id", None) or "",
                "aid": mail.account_id, "domains": domains})).fetchall()
    except Exception as exc:
        _log.warning("email.reply_card_usual_failed", error=type(exc).__name__)
        return []
    out = [str(r.addr) for r in rows
           if r.addr and (int(r.n or 0) >= USUAL_MIN or bool(r.in_thread))
           and str(r.addr).lower() not in skip]
    return sorted(out)


async def _rule_candidates(
    owner: str, mail: Any, people: Sequence[str], *, skip: set[str],
) -> list[tuple[str, str]]:
    """``(colleague, match)`` of the member's always-copy rules for the
    people of the mail (§21.16). A fault reads as none."""
    from gateway.routes.email.automation.always_copy import always_copy_people

    try:
        async with _tenant_session() as db:
            return await always_copy_people(db, owner, mail.account_id, people, skip=skip)
    except Exception as exc:
        _log.warning("email.reply_card_rule_failed", error=type(exc).__name__)
        return []


def _usual_note(added: Sequence[str], mail: Any) -> str:
    """"Added kiran@…, veena@… — you copy them on mail to jain.ac.in"."""
    domain = str(getattr(mail, "sender", "") or "").rpartition("@")[2]
    where = f"on mail to {domain}" if domain else "on this thread"
    return f"Added {', '.join(added)}. You copy them {where}."


#: The mark of a person that a trusted mail of the thread names, and that the
#: answered mail does not (a Cc of a colleague's earlier mail).
IN_THREAD_MARK = "in this email thread"


def _person_marks(
    mail: Any, classes: dict[str, str] | None, asked: Sequence[str],
    carried: Sequence[str], usual: Sequence[str], shown: Sequence[str],
    *, held: bool = False,
) -> dict[str, str]:
    """The words of each person on the card (item 8), by lower case.

    Review P1-b: the server cannot know that the member named a person, so
    an added person never reads "you asked". In a held run it reads "the
    assistant added them", else "added to this reply"."""
    from acb_skills.held_card import (
        ADDED_MARK,
        ASSISTANT_ADDED_MARK,
        NEW_MARK,
        USUAL_COPY_MARK,
        thread_mark,
    )

    ASKED_MARK = ASSISTANT_ADDED_MARK if held else ADDED_MARK  # noqa: N806

    sender = str(getattr(mail, "sender", "") or "").lower()
    who = str(getattr(mail, "sender_name", "") or "") or sender
    on_mail = {a.lower() for a in _mail_people(mail)}
    asked_l = {a.lower() for a in [*asked, *carried]}
    usual_l = {a.lower() for a in usual}
    out: dict[str, str] = {}
    for a in shown:
        low = a.lower()
        cls = (classes or {}).get(low)
        if low in usual_l:
            words = USUAL_COPY_MARK
        elif low in asked_l:
            words = ASKED_MARK
        elif low in on_mail and low != sender:
            words = thread_mark(who)
        elif cls == "thread" and low != sender:
            words = IN_THREAD_MARK
        else:
            words = ""
        if cls == "new":
            words = f"{NEW_MARK} · {words}" if words else NEW_MARK
        if words:
            out[low] = words
    return out


def _reply_record_key(card_id: str) -> Any:
    from acb_common.tenant_redis import key

    return key(REPLY_RECORD_NAMESPACE, str(card_id))


async def _write_reply_record(
    user: UserContext, message_id: str, added: list[str], *,
    bcc: Sequence[str] = (), held: bool = False,
) -> str:
    """Record the added people of one card under a card id that this server
    mints. Returns the id, or "" on a fault, and then the card adds nobody.
    The record also holds the Bcc of the card and the held flag (§21.15)."""
    import uuid

    from acb_common.tenant_redis import organization_scope
    from gateway.routes.email.automation import held_card as hc

    org = str(getattr(user, "organization_id", None) or "")
    if not org:
        return ""
    card_id = uuid.uuid4().hex
    rec = {"owner": (user.email or "").strip().lower(), "message_id": message_id,
           "added": list(added), "bcc": list(bcc), "held": bool(held)}
    try:
        with organization_scope(org):
            done = await hc._tenant_redis().set(
                _reply_record_key(card_id), json.dumps(rec), nx=True,
                ex=REPLY_RECORD_TTL_S)
    except Exception as exc:  # the class only
        _log.warning("email.reply_card_record_write_failed", error=type(exc).__name__)
        return ""
    return card_id if done else ""


async def _take_reply_record(
    user: UserContext, card_id: str, message_id: str,
) -> tuple[list[str], list[str]]:
    """The added people and the Bcc of the card *card_id*, read once
    (``GETDEL``). None of another member, another mail, or a fault: the act
    then takes only the people of the mail, and no Bcc."""
    from acb_common.tenant_redis import organization_scope
    from gateway.routes.email.automation import held_card as hc

    org = str(getattr(user, "organization_id", None) or "")
    if not org or not card_id:
        return [], []
    try:
        with organization_scope(org):
            raw = await hc._tenant_redis().getdel(_reply_record_key(card_id))
    except Exception as exc:
        _log.warning("email.reply_card_record_take_failed", error=type(exc).__name__)
        return [], []
    rec = _record_of(raw, user, message_id)
    if rec is None:
        return [], []
    return ([str(a) for a in rec.get("added") or [] if a],
            [str(a) for a in rec.get("bcc") or [] if a])


def _record_of(raw: Any, user: UserContext, message_id: str) -> dict[str, Any] | None:
    """The record of a card, when it is of THIS member and THIS mail."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        rec = json.loads(raw) if isinstance(raw, str) and raw else None
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    if str(rec.get("owner") or "") != (user.email or "").strip().lower():
        return None
    if _as_uuid(str(rec.get("message_id") or "")) != _as_uuid(message_id):
        return None
    return rec


#: The answer when the record of a card is gone or is not the caller's.
BCC_NO_RECORD = ("This card can no longer take a Bcc. Ask for the reply again to add one.")


@router.post("/reply-card/bcc")
async def reply_card_bcc(
    req: ReplyCardBccRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Add a Bcc that the member typed on the reply card (§21.15).

    The member's browser posts it on the member's own click. No tool calls
    it. The server classes the address with THE classifier and adds it to
    the record of THIS card, so the act still takes a subset of the record
    only. A card of a held chat takes an ``org`` or a ``sent`` person only.
    A person on To or Cc of the card, or the member, answers 409. Nothing
    is sent."""
    from acb_common.tenant_redis import organization_scope
    from acb_skills.held_card import address_problem
    from gateway.routes.email.automation import held_card as hc

    addr = (parseaddr(req.address)[1] or req.address).strip()
    why = address_problem(addr)
    if why:
        raise HTTPException(status_code=422, detail=f"The Bcc address {why}.")
    org = str(getattr(user, "organization_id", None) or "")
    if not org:
        raise HTTPException(status_code=409, detail=BCC_NO_RECORD)
    with organization_scope(org):
        raw = await hc._tenant_redis().get(_reply_record_key(req.card_request_id))
    rec = _record_of(raw, user, req.message_id)
    if rec is None:
        raise HTTPException(status_code=409, detail=BCC_NO_RECORD)
    owner = user.email or "anonymous"
    mail = await _load_mail(req.message_id, owner)
    all_to, all_cc = mail.recipients(True)
    taken = {a.lower() for a in [*all_to, *all_cc, *(rec.get("added") or []),
                                  *getattr(mail, "own", set())]}
    if addr.lower() in taken:
        raise HTTPException(status_code=409,
                            detail=f"{addr} is already on this reply, so it is not a Bcc.")
    classes = await _reply_classes(
        owner, [addr], primary=[mail.sender] if mail.sender else [],
        people=_mail_people(mail), account_id=mail.account_id, thread_id=mail.thread_id)
    cls = classes.get(addr.lower(), "new")
    held = bool(rec.get("held"))
    if held and cls not in HELD_BCC_CLASSES:
        raise HTTPException(status_code=422, detail=HELD_BCC_REFUSED.format(addr=addr))
    bcc = [str(a) for a in rec.get("bcc") or [] if a]
    if addr.lower() not in {a.lower() for a in bcc}:
        if len(bcc) >= MAX_ADDED:
            raise HTTPException(status_code=422, detail="This card holds enough Bcc people.")
        bcc.append(addr)
    rec["bcc"] = bcc
    with organization_scope(org):
        done = await hc._tenant_redis().set(_reply_record_key(req.card_request_id),
                                            json.dumps(rec), xx=True, keepttl=True)
    if not done:
        raise HTTPException(status_code=409, detail=BCC_NO_RECORD)
    _log.info("email.reply_card_bcc_added", cls=cls, held=held)
    marks = _bcc_marks([addr], [addr], {addr.lower(): cls}, held=False)
    return {"address": addr, "class": cls, "mark": marks.get(addr.lower(), "")}


def ticked_subset(derived: Sequence[str], ticked: Sequence[str]) -> list[str] | None:
    """The addresses of *derived* that the member ticked, in the derived order,
    or None when *ticked* names an address that *derived* does not hold.

    THE check of the tick boxes (owner, 2026-10-24). The card shows the
    server's lists, and the member may only take people off them. An added
    address, and an address named twice, send nothing."""
    want = [t.strip().lower() for t in ticked]
    if len(set(want)) != len(want):
        return None
    have = {d.strip().lower() for d in derived}
    if any(t not in have for t in want):
        return None
    chosen = set(want)
    return [d for d in derived if d.strip().lower() in chosen]


def _unticked_new(lists: tuple[list[str], list[str]], classes: dict[str, str]
                  ) -> dict[str, list[str]]:
    """The ticked start of one choice: each address that is not ``new``."""
    from acb_skills.held_card import CLASS_NEW

    to, cc = lists
    keep = [a for a in to if classes.get(a.strip().lower(), CLASS_NEW) != CLASS_NEW]
    return {"to": keep, "cc": [a for a in cc
                               if classes.get(a.strip().lower(), CLASS_NEW) != CLASS_NEW]}


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
    # Item 11 (owner, 2026-10-24): the card shows ONE list, each person of the
    # mail and each person that the card added. So the act derives Reply all
    # always, whatever ``reply_all`` the answer names, and the member's ticks
    # pick from it. The added people come from the server's record of THIS
    # card, read once, never from a request field.
    derived_to, derived_cc = mail.recipients(True)
    added, derived_bcc = await _take_reply_record(user, req.card_request_id, req.message_id)
    derived_cc = _unique([*derived_cc, *added], {a.lower() for a in derived_to})
    # §21.15: the Bcc comes from the record of THIS card too. A Bcc that is
    # also on To or Cc is not one.
    derived_bcc = _unique(derived_bcc, {a.lower() for a in [*derived_to, *derived_cc]})
    # The tick boxes (owner, 2026-10-24): the answer names the people that the
    # member TICKED. Each must be on the server's list. A person that is not
    # on the list, or a list that changed after the card, sends nothing.
    to = ticked_subset(derived_to, choice.to) if derived_to else None
    cc = ticked_subset(derived_cc, choice.cc)
    bcc = ticked_subset(derived_bcc, getattr(choice, "bcc", ()) or ())
    if not to or cc is None or bcc is None:
        _log.warning("email.reply_card_answer_refused", reason="recipients_changed")
        raise HTTPException(
            status_code=409,
            detail=("The card names a person that is not on this mail now, so nothing "
                    "was sent or saved. Ask for the reply again."),
        )
    subject = _reply_subject(mail.subject)
    if choice.action == "send":
        res = await send_email(
            SendEmailRequest(
                account_id=mail.account_id, to=to, cc=cc or None, bcc=bcc or None,
                subject=subject, body_text=body, reply_to_message_id=req.message_id,
            ),
            background, user,
        )
        _log.info("email.reply_card_sent", reply_all=choice.reply_all, bcc=len(bcc),
                  left_out=len(derived_to) + len(derived_cc) + len(derived_bcc)
                  - len(to) - len(cc) - len(bcc))
        return {"ok": True, "action": "send", "to": to, "cc": cc, "mailbox": mail.mailbox,
                "id": (res or {}).get("id"), **({"bcc": bcc} if bcc else {})}
    draft_id = await _save_draft(mail, user.email or owner, to, cc, subject, body,
                                 **({"bcc": bcc} if derived_bcc else {}))
    _log.info("email.reply_card_saved", reply_all=choice.reply_all)
    return {"ok": True, "action": "save", "to": to, "cc": cc, "mailbox": mail.mailbox,
            "draft_id": draft_id, **({"bcc": bcc} if bcc else {})}


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
    bcc: list[str] | None = None,
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
                # Item 9 (owner, 2026-10-24): the draft names exactly the
                # people that the card starts ticked. The card carries the Cc
                # of the thread's draft as added people, so no Cc is lost.
                body=body, exact_to=True, prior=prior, exact_cc=True,
                # §21.15: a card with a Bcc writes the ticked Bcc. A card with
                # none keeps the Bcc of the draft, as before.
                **({"bcc": bcc, "exact_bcc": True} if bcc is not None else {}),
            )
        if chat_wrote and mail.thread_id:
            await _store_ai_draft(db, mail.account_id, mail.thread_id, body,
                                  commit=False)
    return wrote.local_id, wrote.kept_draft


async def _keep_card_draft(
    mail: _Mail, owner: str, to: list[str], cc: list[str], subject: str, body: str,
    *, prior: list[str] | None = None, bcc: list[str] | None = None,
) -> tuple[str, bool]:
    """The draft of a card that shows: best effort, so the card always shows."""
    try:
        return await _write_draft(mail, owner, to, cc, subject, body,
                                  chat_wrote=True, prior=prior, bcc=bcc)
    except Exception as exc:  # the card still shows
        _log.warning("email.reply_card_draft_failed", error=type(exc).__name__)
        return "", False


async def _save_draft(
    mail: _Mail, owner: str, to: list[str], cc: list[str], subject: str, body: str,
    *, bcc: list[str] | None = None,
) -> str:
    """Save the member's reply as the reply draft of the conversation.

    It returns the LOCAL id, which ``/email/drafts/schedule`` and
    ``send_draft`` take. It returned the provider id before, and a schedule
    of that id found no draft. The member chose the Cc on the card, so the
    save writes it as it is."""
    local_id, _kept = await _write_draft(mail, owner, to, cc, subject, body, bcc=bcc)
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
