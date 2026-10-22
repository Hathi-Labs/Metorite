"""Held chat: you confirm new people. The gateway half (``email_app_master_plan.md`` §21).

A held chat read mail from outside senders (H-236), so a model in it may
follow an instruction that a stranger wrote. §21 still lets such a chat send,
through ONE card that THIS module builds and records:

1. ``POST /email/held-card`` builds the card of one send. It reads the draft
   (or, in later slices, the mail to forward, the new mail or the sender to
   unsubscribe from), classifies each person (:func:`classify_addresses`),
   and records the offered rows in tenant Redis under a card id that it
   mints. The model gets no call that edits the record.
2. The email assistant shows the rows on the card, with the card id as the
   card's ``request_id``. ``POST /agent/respond-input`` records the digest
   of the member's answer under the same id (``reply_card.record_reply_answer``).
3. ``POST /email/held-card/act`` takes the answer once, loads the record
   once, and sends ONLY the ticked rows that the record holds. A row that is
   not ticked is not sent, also when the draft holds it.

**Who is known.** The server decides, from data, never from the model:
``org`` (a member or a People row of the bound organization), ``sent`` (a
person that the member wrote to), ``thread`` (the sender of a mail of the
thread of a reply draft) and ``new`` (anything else, a sender of outside mail
included). A known person starts ticked. A new address starts unticked and
carries the mark of ``acb_skills.held_card.NEW_MARK``.

**Dark.** With ``EMAIL_HELD_CARD_SEND`` off, each route here answers 404.

Fences: ``tests/unit/test_held_send_classes.py`` (and its R8 half),
``tests/unit/test_held_send_card_record.py`` and
``tests/unit/test_held_send_no_ticked.py``.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any, Literal

from acb_auth import UserContext, get_current_user
from acb_skills.held_card import (
    CLASS_NEW,
    CLASS_ORG,
    CLASS_SENT,
    CLASS_THREAD,
    DEFAULT_TICKED,
    HELD_TEXT_MAX,
    KIND_BCC,
    KIND_CC,
    KIND_TO,
    RECIPIENT_KINDS,
    address_problem,
    class_mark,
    domain_of,
    held_card_send_enabled,
    lookalike_of,
    lookalike_warning,
    recipient_label,
)
from fastapi import BackgroundTasks, Depends, HTTPException
from gateway.routes.email.automation.drafting import (
    DraftExpect,
    DraftSendRequest,
    DraftUpsertRequest,
    _draft_addresses,
    _refuse_bad_draft_id,
    send_draft_endpoint,
    upsert_draft,
)
from gateway.routes.email.automation.identity import resolve_self
from gateway.routes.email.core import _log, _tenant_session, router
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

#: The Redis namespace of a card record (``cc:<org>:held-card:<card id>``).
RECORD_NAMESPACE = "held-card"
#: A card waits an hour at most, and the act follows at once. The record
#: lives a little longer than the wait, as the answer's record does.
RECORD_TTL_S = 3700
#: The most rows of one card (``ask_tools.MAX_CARD_ROWS``).
MAX_ROWS = 100
#: The longest ``context`` that ``request_confirmation`` shows whole.
CONTEXT_MAX = 4000
#: The longest ``detail``.
DETAIL_MAX = 500

#: How long the act waits for the record of the answer (``reply_card.TAKE_WAIT_S``).
#: respond-input writes it just after the run takes the answer.
ANSWER_WAIT_S = 3.0

#: The answer of every route while the switch is off.
HELD_CARD_OFF = "Not found"

#: The refusal of a Bcc to a new address (open question 5, the safer
#: choice). A held chat cannot Bcc a person that the member does not know,
#: and no tick allows it, because a Bcc is the one recipient that nobody on
#: the mail can see.
BCC_NEW_REFUSED = (
    "A Bcc of this mail goes to a new address, and a chat that read outside "
    "mail cannot Bcc a new address. Nothing was sent. Take the Bcc out with "
    "edit_draft, or ask the member for an address that they know.")
#: The refusal of a text that the card cannot show in full (open question 8).
TEXT_TOO_LONG = (
    f"The text is longer than {HELD_TEXT_MAX} characters, so one card cannot "
    "show all of it, and nothing was sent. Ask the member to shorten it.")
#: The answer when no To row was ticked (§21.4, "a send with no ticked
#: recipient"). The send never falls back to the draft.
NO_TICKED_RECIPIENT = "Not sent: no recipient was ticked."


def _off() -> None:
    """404 unless ``EMAIL_HELD_CARD_SEND`` is on (the one reader)."""
    if not held_card_send_enabled():
        raise HTTPException(status_code=404, detail=HELD_CARD_OFF)


def _owner(user: UserContext) -> str:
    owner = (user.email or "").strip()
    if not owner:
        raise HTTPException(status_code=401, detail="No member.")
    return owner


# ── The classifier (§21.4 item 2) ────────────────────────────────────────────
#
# It reads the same data as ``GET /email/contacts/suggest``: the ``org`` arm
# (``app_user`` and ``people`` of the bound tenant, each predicate naming the
# tenant) and the ``sent`` arm (a To or a Cc of a Sent mail of the member's
# own mailboxes). The R8 fence compares the two on one seeded world. A
# ``seen`` row of the suggest route is a sender only, and it is NEW here.

_TENANT = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

CLASSIFY_SQL = f"""
WITH wanted AS (SELECT DISTINCT LOWER(a) AS addr FROM unnest(CAST(:wanted AS text[])) AS a),
accts AS (SELECT id FROM email_accounts WHERE user_id = :uid),
org AS (
    SELECT LOWER(au.email) AS addr FROM app_user au
     WHERE COALESCE(au.status, 'active') = 'active'
       AND au.organization_id = {_TENANT}
       AND LOWER(au.email) IN (SELECT addr FROM wanted)
    UNION
    SELECT LOWER(pp.email) FROM people pp
     WHERE pp.email IS NOT NULL
       AND COALESCE(pp.status, 'active') = 'active'
       AND NOT EXISTS (
           SELECT 1 FROM app_user ux
            WHERE LOWER(ux.email) = LOWER(pp.email)
              AND ux.status IN ('suspended', 'removed'))
       AND pp.organization_id = {_TENANT}
       AND LOWER(pp.email) IN (SELECT addr FROM wanted)
),
sent AS (
    SELECT DISTINCT LOWER(r->>'email') AS addr
      FROM email_messages em
      CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(em.to_addresses, '[]'::jsonb)
            || COALESCE(em.cc_addresses, '[]'::jsonb)) AS r
     WHERE em.account_id IN (SELECT id FROM accts)
       AND LOWER(COALESCE(em.folder, '')) = 'sent'
       AND LOWER(r->>'email') IN (SELECT addr FROM wanted)
),
own AS (
    SELECT LOWER(email_address) AS addr FROM email_accounts WHERE user_id = :uid
)
SELECT w.addr,
       (w.addr IN (SELECT addr FROM org) OR w.addr IN (SELECT addr FROM own)) AS is_org,
       (w.addr IN (SELECT addr FROM sent)) AS is_sent
  FROM wanted w
"""

#: The domains that the member knows, for the lookalike flag: the domains of
#: the organization's members, of the member's own mailboxes, and of the
#: people that the newest 2000 Sent mails went to.
KNOWN_DOMAINS_SQL = f"""
SELECT DISTINCT d FROM (
    SELECT SPLIT_PART(LOWER(au.email), '@', 2) AS d FROM app_user au
     WHERE COALESCE(au.status, 'active') = 'active'
       AND au.organization_id = {_TENANT}
    UNION
    SELECT SPLIT_PART(LOWER(email_address), '@', 2) FROM email_accounts
     WHERE user_id = :uid
    UNION
    SELECT SPLIT_PART(LOWER(r->>'email'), '@', 2)
      FROM (SELECT em.to_addresses, em.cc_addresses FROM email_messages em
             WHERE em.account_id IN (
                   SELECT id FROM email_accounts WHERE user_id = :uid)
               AND LOWER(COALESCE(em.folder, '')) = 'sent'
             ORDER BY em.received_at DESC NULLS LAST LIMIT 2000) s
      CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(s.to_addresses, '[]'::jsonb)
            || COALESCE(s.cc_addresses, '[]'::jsonb)) AS r
) k WHERE d <> '' LIMIT 5000
"""

#: The senders of the mails of one thread of one mailbox: the people that a
#: reply on that thread answers (``thread``). A draft, a Sent mail and the
#: member's own address are not senders of the thread.
THREAD_SENDERS_SQL = """
SELECT DISTINCT LOWER(em.from_address->>'email') AS addr
  FROM email_messages em
 WHERE em.account_id = CAST(:aid AS uuid)
   AND em.thread_id = :tid
   AND LOWER(COALESCE(em.folder, '')) NOT IN ('drafts', 'draft', 'sent')
   AND em.from_address->>'email' IS NOT NULL
"""


async def classify_addresses(
    db: Any, owner: str, addresses: Iterable[str], *,
    thread_senders: Iterable[str] = (),
) -> dict[str, str]:
    """The class of each address, keyed by its lower-case form.

    THE classifier. It takes no class from the caller and calls no model.
    *thread_senders* are the senders of the thread, which the server read
    (:data:`THREAD_SENDERS_SQL`). The strongest class wins: ``org``, then
    ``sent``, then ``thread``, then ``new``."""
    wanted = sorted({str(a).strip().lower() for a in addresses if str(a or "").strip()})
    if not wanted:
        return {}
    rows = (await db.execute(text(CLASSIFY_SQL), {"wanted": wanted, "uid": owner})).fetchall()
    found = {str(r.addr): r for r in rows}
    senders = {str(a).strip().lower() for a in thread_senders if a}
    out: dict[str, str] = {}
    for addr in wanted:
        r = found.get(addr)
        if r is not None and bool(r.is_org):
            out[addr] = CLASS_ORG
        elif r is not None and bool(r.is_sent):
            out[addr] = CLASS_SENT
        elif addr in senders:
            out[addr] = CLASS_THREAD
        else:
            out[addr] = CLASS_NEW
    return out


async def known_domains(db: Any, owner: str) -> frozenset[str]:
    """The domains that the member knows (:data:`KNOWN_DOMAINS_SQL`)."""
    rows = (await db.execute(text(KNOWN_DOMAINS_SQL), {"uid": owner})).fetchall()
    return frozenset(str(r.d) for r in rows if getattr(r, "d", None))


async def thread_senders(db: Any, account_id: str, thread_id: str | None) -> list[str]:
    """The senders of the thread *thread_id* of the mailbox, or none."""
    if not thread_id:
        return []
    rows = (await db.execute(text(THREAD_SENDERS_SQL),
                             {"aid": account_id, "tid": thread_id})).fetchall()
    return [str(r.addr) for r in rows if getattr(r, "addr", None)]


def recipient_rows(
    lists: dict[str, list[str]], classes: dict[str, str], known: Iterable[str],
) -> list[dict[str, Any]]:
    """The recorded rows of the recipients, To first, then Cc, then Bcc.

    Each row holds its id, its kind, its address, its class, whether it
    starts ticked, the label and mark of the card, and a lookalike warning
    for a new address. A known person starts ticked, a new one unticked."""
    known = frozenset(known)
    rows: list[dict[str, Any]] = []
    for kind in RECIPIENT_KINDS:
        for i, addr in enumerate(lists.get(kind) or []):
            cls = classes.get(addr.strip().lower(), CLASS_NEW)
            row: dict[str, Any] = {
                "id": f"{kind}-{i}",
                "kind": kind,
                "address": addr.strip(),
                "class": cls,
                "checked": cls in DEFAULT_TICKED,
                "label": recipient_label(kind, addr.strip()),
                "hint": class_mark(cls),
            }
            if cls == CLASS_NEW:
                like = lookalike_of(domain_of(addr), known)
                if like:
                    row["warning"] = lookalike_warning(like)
            rows.append(row)
    return rows


def card_rows(record_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows that the tool hands to ``request_confirmation``: the
    recorded rows, without the address or the file reference that only the
    act route reads."""
    out = []
    for r in record_rows:
        row = {"id": r["id"], "label": r["label"], "hint": r.get("hint") or "",
               "checked": bool(r["checked"]), "kind": r["kind"]}
        if r.get("class"):
            row["class"] = r["class"]
        if r.get("warning"):
            row["warning"] = r["warning"]
        out.append(row)
    return out


# ── The files rule (§21.1 item 3, §21.3 the Attach row) ─────────────────────
#
# A file row resolves only from data of the member's own mailbox: an
# attachment of a mail of the sending mailbox, owned by the member, in the
# bound tenant. The model names ``attachment:<id>``, and the server reads the
# name, the size and the mail. A workspace path, a Metorite file, a file of
# another member and a file of another tenant send nothing.

#: The files of mails of the member's own sending mailbox, by id. The owner
#: join keeps a file of another member out, and RLS keeps another tenant out.
OWNED_FILES_SQL = (
    "SELECT ea.id::text AS id, ea.filename, ea.size_bytes, "
    "em.id::text AS message_id, em.subject "
    "FROM email_attachments ea "
    "JOIN email_messages em ON em.id = ea.message_id "
    "JOIN email_accounts acc ON acc.id = em.account_id "
    "WHERE ea.id = ANY(CAST(:ids AS uuid[])) AND acc.user_id = :uid "
    "AND em.account_id = CAST(:aid AS uuid)"
)
#: The files of one mail of the member's own sending mailbox.
MAIL_FILES_SQL = (
    "SELECT ea.id::text AS id, ea.filename, ea.size_bytes, "
    "em.id::text AS message_id, em.subject "
    "FROM email_attachments ea "
    "JOIN email_messages em ON em.id = ea.message_id "
    "JOIN email_accounts acc ON acc.id = em.account_id "
    "WHERE em.id = CAST(:mid AS uuid) AND acc.user_id = :uid "
    "AND em.account_id = CAST(:aid AS uuid) ORDER BY ea.created_at, ea.filename"
)
#: The most bytes of the files of one held send. A larger set sends nothing.
MAX_FILES_BYTES = 20 * 1024 * 1024
#: The answer when a named file is not a file of the member's own mailbox.
FILE_NOT_OWNED = ("A named file is not a file of a mail of this mailbox of the member, "
                  "so nothing was sent.")


def file_rows(files: Sequence[Any]) -> list[dict[str, Any]]:
    """The recorded rows of *files*: each starts ticked, and the member can
    untick it. The label names the file and its size, and the hint names the
    mail that holds it."""
    from acb_skills.held_card import KIND_FILE, file_label

    total = sum(int(getattr(f, "size_bytes", 0) or 0) for f in files)
    if total > MAX_FILES_BYTES:
        raise HTTPException(
            status_code=422,
            detail=(f"The files are larger than {MAX_FILES_BYTES // (1024 * 1024)} MB "
                    "together, so nothing was sent."))
    rows = []
    for i, f in enumerate(files):
        subject = " ".join(str(getattr(f, "subject", "") or "(no subject)").split())[:120]
        rows.append({
            "id": f"file-{i}",
            "kind": KIND_FILE,
            "attachment_id": str(f.id),
            "label": file_label(str(f.filename or "file"), _size_text(f.size_bytes)),
            "hint": f'From the mail "{subject}" of your own mailbox.',
            "checked": True,
        })
    return rows


async def resolve_file_refs(
    db: Any, owner: str, account_id: str, refs: Sequence[str],
) -> list[Any]:
    """The files that *refs* name, each a file of the member's own mailbox.

    THE resolver of a file of a held send. Each ref must be
    ``attachment:<id>`` (``acb_skills.held_card.parse_file_ref``), and each id
    must be a file of a mail of the sending mailbox of *owner*. Else 422 or
    404, and nothing is sent."""
    from acb_skills.held_card import parse_file_ref

    ids: list[str] = []
    for ref in refs:
        try:
            _source, fid = parse_file_ref(ref)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if fid not in ids:
            ids.append(fid)
    if not ids:
        return []
    rows = (await db.execute(text(OWNED_FILES_SQL),
                             {"ids": ids, "uid": owner, "aid": account_id})).fetchall()
    found = {str(r.id): r for r in rows}
    if any(i not in found for i in ids):
        raise HTTPException(status_code=404, detail=FILE_NOT_OWNED)
    return [found[i] for i in ids]


async def mail_files(db: Any, owner: str, account_id: str, message_id: str) -> list[Any]:
    """The files of one mail of the member's own sending mailbox."""
    return list((await db.execute(text(MAIL_FILES_SQL), {
        "mid": message_id, "uid": owner, "aid": account_id})).fetchall())


def ticked_file_ids(rows: Sequence[dict[str, Any]], ids: frozenset[str]) -> list[str]:
    """The attachment ids of the ticked RECORDED file rows, in card order."""
    return [str(r["attachment_id"]) for r in rows
            if r["kind"] == "file" and r["id"] in ids and r.get("attachment_id")]


async def file_payloads(user: UserContext, attachment_ids: Sequence[str]) -> list[dict[str, Any]]:
    """The bytes of each ticked file, through the ONE owned fetch of a file
    (``attachments._fetch_owned_attachment``), as attachments of a new mail.
    The owned fetch checks the owner again."""
    import base64

    from gateway.routes.email.transport.attachments import _fetch_owned_attachment

    out: list[dict[str, Any]] = []
    async with _tenant_session() as db:
        for aid in attachment_ids:
            got = await _fetch_owned_attachment(db, aid, user, max_bytes=MAX_FILES_BYTES)
            out.append({
                "filename": str(got.row.filename or "file"),
                "mime_type": str(got.row.mime_type or "application/octet-stream"),
                "content_b64": base64.b64encode(got.content or b"").decode("ascii"),
            })
    return out


# ── The record (§21.4 item 1) ────────────────────────────────────────────────


def _tenant_redis() -> Any:
    """The tenant Redis client. A seam, so a test passes a fake client."""
    from acb_common.tenant_redis import get_tenant_redis

    return get_tenant_redis()


def _record_key(card_id: str) -> Any:
    from acb_common.tenant_redis import key

    return key(RECORD_NAMESPACE, str(card_id))


def _org_of(user: UserContext) -> str:
    """The tenant of the session (R5). Never a request field."""
    org = str(getattr(user, "organization_id", None) or "")
    if not org:
        raise HTTPException(status_code=403, detail="No organization in this session.")
    return org


async def write_record(user: UserContext, record: dict[str, Any]) -> str:
    """Record one card, under a card id that this server mints. Returns it.

    ``SET NX`` with a TTL, in the session's own tenant. A fault records
    nothing, and the route answers 503, so no card shows."""
    from acb_common.tenant_redis import organization_scope

    card_id = uuid.uuid4().hex
    try:
        with organization_scope(_org_of(user)):
            done = await _tenant_redis().set(
                _record_key(card_id), json.dumps(record), nx=True, ex=RECORD_TTL_S)
    except HTTPException:
        raise
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("email.held_card_record_failed", error=type(exc).__name__)
        raise HTTPException(status_code=503, detail="The card could not be recorded.") from None
    if not done:
        raise HTTPException(status_code=503, detail="The card could not be recorded.")
    return card_id


async def take_record(user: UserContext, card_id: str) -> dict[str, Any] | None:
    """Read and delete one card record (``GETDEL``), so a card acts once.
    None for no record, a fault, or a record of another member."""
    from acb_common.tenant_redis import organization_scope

    try:
        with organization_scope(_org_of(user)):
            raw = await _tenant_redis().getdel(_record_key(card_id))
    except HTTPException:
        raise
    except Exception as exc:
        _log.warning("email.held_card_take_failed", error=type(exc).__name__)
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    if str(rec.get("owner") or "").lower() != (user.email or "").strip().lower():
        return None
    return rec


# ── The card of a saved draft: send it now, or later ─────────────────────────

#: The draft of a member's own mailbox, with what its card shows.
DRAFT_ROW_SQL = (
    "SELECT em.id::text AS id, em.account_id::text AS account_id, em.subject, "
    "em.to_addresses, em.cc_addresses, em.bcc_addresses, em.body_text, "
    "em.thread_id, em.has_attachments "
    "FROM email_messages em JOIN email_accounts ea ON ea.id = em.account_id "
    "WHERE em.id = CAST(:id AS uuid) AND em.account_id = CAST(:aid AS uuid) "
    "AND ea.user_id = :uid AND LOWER(em.folder) IN ('drafts', 'draft')"
)

#: The names and sizes of the files of one draft, for the card text.
DRAFT_FILES_SQL = (
    "SELECT filename, size_bytes FROM email_attachments "
    "WHERE message_id = CAST(:id AS uuid) ORDER BY created_at, filename")


class HeldCardRequest(BaseModel):
    """One send that a held chat asks for. The server reads the rest.

    No recipient, no body and no class: for a draft, the row holds them, and
    the server classifies them. ``extra="forbid"`` makes any other field a
    422."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["draft", "schedule"]
    account_id: str = Field(max_length=64)
    draft_id: str = Field(default="", max_length=64)
    send_at: str = Field(default="", max_length=64)


class HeldCardActRequest(BaseModel):
    """The member's answer to a held card, as the browser posted it.

    ``card_id`` is the id that ``POST /email/held-card`` minted, and the
    card's ``request_id``. ``card_thread_id`` is the thread that owns the
    card, which keys the record of the answer. No recipient, no body and no
    file: a 422 refuses each extra field."""

    model_config = ConfigDict(extra="forbid")

    card_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")
    card_thread_id: str = Field(max_length=200)
    answer: str = Field(max_length=10_000)


def _size_text(size: Any) -> str:
    try:
        n = int(size)
    except (TypeError, ValueError):
        return ""
    if n < 1024 * 1024:
        return f"{max(1, round(n / 1024))} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _check_text(body: str) -> str:
    """The text of a card, cleaned as the reply card is. A text longer than
    :data:`HELD_TEXT_MAX` is refused, never cut (open question 8)."""
    from acb_skills.reply_card import clean_reply_body

    shown = clean_reply_body(body or "")
    if len(shown) > HELD_TEXT_MAX:
        raise HTTPException(status_code=422, detail=TEXT_TOO_LONG)
    return shown


def _lists_of(row: Any) -> dict[str, list[str]]:
    return {
        KIND_TO: _draft_addresses(row.to_addresses),
        KIND_CC: _draft_addresses(row.cc_addresses),
        KIND_BCC: _draft_addresses(row.bcc_addresses),
    }


def _check_lists(lists: dict[str, list[str]]) -> None:
    total = sum(len(v) for v in lists.values())
    if not lists.get(KIND_TO):
        raise HTTPException(status_code=422, detail="The draft has no To recipient.")
    if total > MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"The draft has more than {MAX_ROWS} recipients, so one card cannot show them.")
    for addrs in lists.values():
        for a in addrs:
            why = address_problem(a)
            if why:
                raise HTTPException(
                    status_code=422,
                    detail=f"An address of the draft {why}, so the card cannot show it.")


def _refuse_new_bcc(rows: Sequence[dict[str, Any]]) -> None:
    if any(r["kind"] == KIND_BCC and r["class"] == CLASS_NEW for r in rows):
        raise HTTPException(status_code=422, detail=BCC_NEW_REFUSED)


def _when(send_at: str) -> datetime:
    from gateway.routes.email.automation import send_later

    if not send_later.send_later_enabled():
        raise HTTPException(status_code=404, detail="Send later is not on.")
    return send_later._when(send_at)


def _stamp(when: datetime) -> str:
    return when.strftime("%a %d %b %Y, %H:%M (%z)")


async def _draft_card(req: HeldCardRequest, user: UserContext) -> dict[str, Any]:
    """Build and record the card of one saved draft (``draft`` or ``schedule``)."""
    owner = _owner(user)
    _refuse_bad_draft_id(req.draft_id)
    _refuse_bad_draft_id(req.account_id)
    when = _when(req.send_at) if req.kind == "schedule" else None
    async with _tenant_session() as db:
        row = (await db.execute(text(DRAFT_ROW_SQL), {
            "id": req.draft_id, "aid": req.account_id, "uid": owner})).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Draft not found")
        lists = _lists_of(row)
        _check_lists(lists)
        senders = await thread_senders(db, req.account_id, row.thread_id)
        classes = await classify_addresses(
            db, owner, [a for v in lists.values() for a in v], thread_senders=senders)
        known = await known_domains(db, owner)
        me = await resolve_self(db, req.account_id)
        files = []
        if row.has_attachments:
            files = (await db.execute(text(DRAFT_FILES_SQL), {"id": req.draft_id})).fetchall()
    rows = recipient_rows(lists, classes, known)
    _refuse_new_bcc(rows)
    body = _check_text(str(row.body_text or ""))
    mailbox = (f"{me.label} · {me.address}" if me.label and me.label != me.address
               else me.address)
    subject = " ".join(str(row.subject or "(no subject)").split())[:200]
    lines = [f"Subject: {subject}", "", "The text of the draft:", body or "(no text)"]
    if files:
        lines += ["", "The files of the draft go with it:"]
        lines += [f'- file "{f.filename}" ({_size_text(f.size_bytes)})' for f in files[:20]]
        if len(files) > 20:
            lines.append(f"- +{len(files) - 20} more")
    context = "\n".join(lines)
    if len(context) > CONTEXT_MAX:
        raise HTTPException(status_code=422, detail=TEXT_TOO_LONG)
    new = sum(1 for r in rows if r["class"] == CLASS_NEW)
    detail = (f"From {mailbox} · {len(rows)} recipient(s). Only a ticked person gets "
              f"the mail. {new} new address(es) start unticked.")[:DETAIL_MAX]
    title = f"Send this draft at {_stamp(when)}?" if when else "Send this draft?"
    record = {
        "v": 1, "kind": req.kind, "owner": owner.lower(),
        "account_id": req.account_id, "draft_id": req.draft_id,
        "send_at": when.isoformat() if when else "",
        "held": lists, "rows": rows,
    }
    card_id = await write_record(user, record)
    _log.info("email.held_card_built", kind=req.kind, rows=len(rows), new=new)
    return {"card_id": card_id, "title": title, "detail": detail, "context": context,
            "rows": card_rows(rows)}


@router.post("/held-card")
async def held_card(
    req: HeldCardRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Build, classify and record the card of one send of a held chat.

    The answer is what the card shows: ``card_id``, ``title``, ``detail``,
    ``context`` and ``rows``. Nothing is sent. 404 while the switch is off."""
    _off()
    return await _draft_card(req, user)


# ── The act (§21.4 item 4) ───────────────────────────────────────────────────


def _same(a: dict[str, list[str]], b: dict[str, list[str]]) -> bool:
    return all({x.lower() for x in a.get(k) or []} == {x.lower() for x in b.get(k) or []}
               for k in RECIPIENT_KINDS)


def _ticked_lists(rows: Sequence[dict[str, Any]], ids: frozenset[str]) -> dict[str, list[str]]:
    """The recipients of the ticked RECORDED rows, by kind, in card order."""
    out: dict[str, list[str]] = {k: [] for k in RECIPIENT_KINDS}
    for r in rows:
        if r["id"] in ids and r["kind"] in out:
            out[r["kind"]].append(str(r["address"]))
    return out


def _audit(card_id: str, kind: str, rows: Sequence[dict[str, Any]], ids: frozenset[str]) -> None:
    """The one audit line of an act: the card id and the count of ticked rows
    by class. No address and no text reach the log."""
    ticked: dict[str, int] = {}
    for r in rows:
        if r["id"] in ids:
            key = str(r.get("class") or r["kind"])
            ticked[key] = ticked.get(key, 0) + 1
    _log.info("email.held_card_acted", card=card_id[:12], kind=kind,
              ticked=ticked, left_out=len(rows) - len(ids))


async def _narrow_draft(
    user: UserContext, rec: dict[str, Any], ticked: dict[str, list[str]],
) -> str:
    """Take each unticked recipient off the draft, before the send.

    The row must still hold the recipients that the card showed. Else 409,
    and nothing is sent. The write is the draft save that ``edit_draft``
    uses (``PUT /email/drafts`` with ``keep_body``), so the text, the HTML
    and the files of the draft stay. Returns the local id of the draft."""
    from gateway.routes.email.transport.messages import get_full_body

    owner = _owner(user)
    async with _tenant_session() as db:
        row = (await db.execute(text(DRAFT_ROW_SQL), {
            "id": rec["draft_id"], "aid": rec["account_id"], "uid": owner})).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    if not _same(_lists_of(row), rec["held"]):
        raise HTTPException(
            status_code=409,
            detail="The recipients of this draft changed after the card. Nothing was sent.")
    full = await get_full_body(rec["draft_id"], user)
    saved = await upsert_draft(DraftUpsertRequest(
        account_id=rec["account_id"], draft_id=rec["draft_id"],
        to=ticked[KIND_TO], cc=ticked[KIND_CC], bcc=ticked[KIND_BCC],
        subject=str(row.subject or ""), body=str((full or {}).get("body_text") or ""),
        keep_html=True, keep_body=True,
    ), user)
    return str((saved or {}).get("id") or rec["draft_id"])


async def _act_draft(
    rec: dict[str, Any], ticked: dict[str, list[str]], background: BackgroundTasks,
    user: UserContext,
) -> dict[str, Any]:
    draft_id = str(rec["draft_id"])
    if not _same(ticked, rec["held"]):
        draft_id = await _narrow_draft(user, rec, ticked)
    expect = DraftExpect(to=ticked[KIND_TO], cc=ticked[KIND_CC], bcc=ticked[KIND_BCC])
    if rec["kind"] == "schedule":
        from gateway.routes.email.automation.send_later import (
            ScheduleRequest,
            schedule_draft,
        )

        row = await schedule_draft(ScheduleRequest(
            account_id=rec["account_id"], draft_id=draft_id,
            send_at=str(rec["send_at"]), expect=expect), user)
        return {"ok": True, "action": "schedule", "schedule_id": row.id,
                "send_at": row.send_at, **ticked}
    await send_draft_endpoint(DraftSendRequest(
        account_id=rec["account_id"], draft_id=draft_id, expect=expect), background, user)
    return {"ok": True, "action": "send", **ticked}


@router.post("/held-card/act")
async def held_card_act(
    req: HeldCardActRequest,
    background: BackgroundTasks,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Do what the member ticked on one held card, and nothing more.

    1. Take the answer once (``reply_card.take_reply_answer``). A miss, a
       second answer, another member, another thread or another text sends
       nothing.
    2. Take the card record once. Check the answer against the RECORDED ids
       (``ask_tools.ticked_rows``). A forged id sends nothing, and nothing
       is trimmed to fit.
    3. Build the send from the ticked recorded rows only.
    4. Check the draft against the card, and send (H-374's ``expect``).
    5. Write one audit line of counts.
    404 while the switch is off."""
    _off()
    from acb_skills.ask_tools import ticked_rows
    from acb_skills.reply_card import take_reply_answer

    owner = _owner(user).lower()
    org = _org_of(user)
    if not await take_reply_answer(org, req.card_id, owner, req.answer, req.card_thread_id,
                                   wait_s=ANSWER_WAIT_S):
        _log.warning("email.held_card_refused", reason="no_record")
        raise HTTPException(
            status_code=409,
            detail="This send was not confirmed on its card, so nothing was sent.")
    rec = await take_record(user, req.card_id)
    if rec is None:
        _log.warning("email.held_card_refused", reason="no_card")
        raise HTTPException(
            status_code=409,
            detail="The card of this send has expired or was used, so nothing was sent.")
    rows = [r for r in rec.get("rows") or [] if isinstance(r, dict)]
    ids = ticked_rows(req.answer, frozenset(str(r["id"]) for r in rows))
    if ids is None:
        _log.warning("email.held_card_refused", reason="bad_ids")
        raise HTTPException(
            status_code=409,
            detail="The answer names a row that the card did not offer, so nothing was sent.")
    ticked = _ticked_lists(rows, ids)
    if not ticked[KIND_TO]:
        _log.info("email.held_card_refused", reason="no_to")
        raise HTTPException(status_code=422, detail=NO_TICKED_RECIPIENT)
    _refuse_new_bcc([r for r in rows if r["id"] in ids])
    out = await _act_draft(rec, ticked, background, user)
    _audit(req.card_id, str(rec["kind"]), rows, ids)
    return {**out, "left_out": len(rows) - len(ids)}
