"""Transport · forward — forward one email, with its original files.

Owner report, 2026-10-09: the owner asked the assistant to forward a BQ email
with its PDF to a colleague. The answer was "my send tool can send a new
message or a reply, but it has no forward operation that re-attaches the
original PDF". Nothing in the backend forwarded a mail. The rule action
FORWARD writes a text-quoted draft, and the reading pane's Forward drops the
files. Spec: ``email_app_master_plan.md`` §15.

``POST /email/forward`` sends a NEW mail from the mailbox that holds the
original, in this order:

1. The owner read. The source must be a mail of a mailbox of the caller, or
   the answer is 404 before any other read. A named ``account_id`` that is not
   the mailbox of the mail is 404 too, as for a reply (EM-T8a, D-EM-19).
2. The files. ``include_attachments`` (default true) takes every file of the
   mail. ``attachment_ids`` takes the named files only, and each must be a
   file of THIS mail. An IMAP mailbox forwards no file (422).
3. The note and the signature of the mailbox.
4. The send, by one of two paths:

   a. **Outlook with every file** (``forwards_natively``): Graph's own
      ``POST /me/messages/{id}/forward``. Graph copies the files at the
      server, so a forward of any size sends no file through Metorite. Its
      ``/me/sendMail`` takes files inline and refuses a body over about
      4 MB, which is why the first build failed there after the approval.
      Graph's forward carries every file or none, so a SUBSET of the files
      on Outlook answers 422 (:data:`OUTLOOK_SUBSET`). That is a choice:
      a forward with some files, rebuilt here, would meet the same inline
      limit again.
   b. **Every other forward** rebuilds the mail. The stored sizes are checked
      against :data:`MAX_FORWARD_BYTES` before any fetch (413). Each file
      comes through ``_fetch_owned_attachment``, the ONE owned fetch of a
      file. A file that the provider gives no bytes for (an attached mail, a
      cloud link) stops the forward with 422, so a forward never goes out
      without a file the member asked for (H-201 part 3). The body is the
      note, a standard forwarded header (From, Date, Subject, To), then the
      original. The HTML of the original is kept: the stored HTML, then the
      HTML cache of the reading pane, then one read of the provider.

   Both paths answer a mail that is too large with 413 (``_mail_too_large``,
   and ``OutlookMailTooLarge`` for Graph). Every other provider failure gets
   a reason (:func:`_provider_refusal`): the provider's own 401, 404 or 429,
   a 422 for any other refusal, and a 502 when the provider is down. Never a
   bare 500.

What a forward does NOT do: it threads into nothing (a forward is a new
conversation), it writes no row of ``email_messages`` (the next sync brings
the sent mail in), and it never takes the acting member from the body.

The send and its confirmation card are the agent's (``forward_email`` in
``apps/agents/agent-email-assistant/agents.py``), as for ``send_email``.
``POST /email/send`` writes no audit row and checks no per-member send right
beyond the ``email`` feature and the mailbox owner. This route has the same
two guards, and it logs ``email.forwarded`` with ids and counts, never an
address or a word of the mail.

Fence: ``tests/unit/test_email_forward.py`` (hermetic) and
``tests/unit/test_email_forward_r8.py`` (R8, a real database).
"""

from __future__ import annotations

import html as _html
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import httpx
from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.email.core import (
    _log,
    _tenant_session,
    provider_session,
    router,
)
from gateway.routes.email.transport.attachments import (
    _canonical_uuid,
    _fetch_owned_attachment,
)
from pydantic import BaseModel, Field
from sqlalchemy import text

#: The most bytes of files that one forward carries. Gmail takes 25 MB of
#: files in one mail, and a bigger mail fails at the provider after the bytes
#: were fetched. So the route refuses above it, before any fetch it can avoid.
MAX_FORWARD_BYTES = 25 * 1024 * 1024

#: The 404 of a forward that names a mailbox that does not hold the mail.
FORWARD_NOT_IN_MAILBOX = (
    "The mail to forward is not in that mailbox. Forward it from the mailbox "
    "that holds it, or leave account_id out."
)

#: The 422 of a subset of the files on an Outlook mailbox (see 4a above).
OUTLOOK_SUBSET = (
    "On an Outlook mailbox, a forward carries every file of the email or "
    "none of them, so nothing was sent. Forward it with all its files, or "
    "with none."
)

_IMAP_FILES = (
    "I cannot forward the files of an IMAP mailbox yet, so nothing was sent. "
    "Forward it without the files, or forward it from the mail app."
)


class ForwardEmailRequest(BaseModel):
    """The body of ``POST /email/forward``. The member comes from the session."""

    message_id: str
    to: list[str] = Field(min_length=1)
    cc: list[str] | None = None
    bcc: list[str] | None = None
    note: str | None = None
    include_attachments: bool = True
    #: Forward only these files of the mail. Wins over ``include_attachments``.
    attachment_ids: list[str] | None = None
    #: The mailbox that sends. It must hold the mail. Absent: the mail's own.
    account_id: str | None = None


# ── The subject ─────────────────────────────────────────────────────────────

#: Each reply, forward or "Subject:" mark at the head of a subject. Mail
#: clients stack them ("Fwd: RE: Fw: X"), and a subject copied with its label
#: gives the "Re: Subject:" that the owner saw.
_SUBJECT_MARKS = re.compile(
    r"^\s*(?:(?:re|fwd?|subject)\s*(?:\[\d+\])?\s*:\s*)+", re.IGNORECASE)


def forward_subject(subject: str | None) -> str:
    """``Fwd: <subject>``, with every reply, forward and "Subject:" mark that
    led the original removed. A mail with no subject gives
    ``Fwd: (no subject)``."""
    core = _SUBJECT_MARKS.sub("", subject or "").strip()
    return f"Fwd: {core}" if core else "Fwd: (no subject)"


# ── The body ────────────────────────────────────────────────────────────────

_FORWARD_LINE = "---------- Forwarded message ---------"
_BODY_INNER = re.compile(r"<body[^>]*>(.*)</body>", re.IGNORECASE | re.DOTALL)


def _address(value: Any) -> str:
    """``Name <address>`` for one address of a mail row, else ``""``."""
    if not isinstance(value, dict):
        return ""
    name = str(value.get("name") or "").strip()
    mail = str(value.get("email") or "").strip()
    return f"{name} <{mail}>" if name and mail else (mail or name)


def _addresses(values: Any) -> str:
    items = values if isinstance(values, list) else []
    return ", ".join(a for a in (_address(v) for v in items) if a)


def _when(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%a, %d %b %Y at %H:%M %Z").strip()
    return str(value or "")


def forward_header(row: Any) -> list[tuple[str, str]]:
    """The forwarded header of the original, as (label, value) pairs."""
    pairs = [
        ("From", _address(row.from_address)),
        ("Date", _when(row.received_at)),
        ("Subject", str(row.subject or "")),
        ("To", _addresses(row.to_addresses)),
    ]
    cc = _addresses(row.cc_addresses)
    if cc:
        pairs.append(("Cc", cc))
    return pairs


def forward_bodies(
    note_text: str,
    note_html: str | None,
    header: list[tuple[str, str]],
    original_text: str,
    original_html: str | None,
) -> tuple[str, str]:
    """The text and the HTML of a forward: the note, then the forwarded
    header, then the original. The original HTML goes in as it is, inside a
    ``gmail_quote`` block, so a reply to the forward quotes it as a client
    does (``quoting.split_quoted_html`` knows the block)."""
    head_text = "\n".join(f"{k}: {v}" for k, v in header)
    note_part = f"{note_text.rstrip()}\n\n" if note_text.strip() else ""
    body_text = f"{note_part}{_FORWARD_LINE}\n{head_text}\n\n{original_text or ''}".rstrip() + "\n"

    head_html = "<br>".join(
        f"{_html.escape(k)}: {_html.escape(v)}" for k, v in header)
    if original_html and original_html.strip():
        inner = _BODY_INNER.search(original_html)
        orig_html = inner.group(1) if inner else original_html
    else:
        orig_html = _html.escape(original_text or "").replace("\n", "<br>")
    note_block = f"<div>{note_html}</div><br>" if note_html and note_html.strip() else ""
    body_html = (
        f"{note_block}<div class=\"gmail_quote\">{_html.escape(_FORWARD_LINE)}<br>"
        f"{head_html}<br><br>{orig_html}</div>"
    )
    return body_text, body_html


# ── The route ───────────────────────────────────────────────────────────────


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _too_big(total: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=(
            f"The files of this mail come to {_mb(total)}, and a forward "
            f"carries {_mb(MAX_FORWARD_BYTES)} at most, so nothing was sent. "
            "Forward it with fewer files, or without them."
        ),
    )


@router.post("/forward")
async def forward_email(
    req: ForwardEmailRequest,
    user: UserContext = Depends(get_current_user),
):
    """Forward one of the caller's own mails, with its original files.

    Answers ``{"id", "ok", "subject", "attachments", "bytes"}``. 404 for a
    mail or a file that is not the caller's, 413 for a mail that is too large,
    422 for a file the provider gives no bytes for or a subset on Outlook, and
    a reason for each other provider failure (:func:`_provider_refusal`).
    """
    owner = user.email or "anonymous"
    mid = _canonical_uuid(req.message_id)
    if mid is None:
        raise HTTPException(status_code=404, detail="Email not found")
    if not any(a.strip() for a in req.to):
        raise HTTPException(status_code=422, detail="A forward needs a recipient in `to`.")

    async with _tenant_session() as db:
        # 1. The owner read. The mail and its mailbox, or 404.
        row = (await db.execute(text(
            """SELECT em.id, em.account_id, em.provider_message_id, em.subject,
                      em.from_address, em.to_addresses, em.cc_addresses,
                      em.received_at, em.body_text, em.body_html, em.snippet,
                      ea.provider
                 FROM email_messages em
                 JOIN email_accounts ea ON ea.id = em.account_id
                WHERE em.id = CAST(:mid AS uuid) AND ea.user_id = :uid"""
        ), {"mid": mid, "uid": owner})).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Email not found")
        account_id = str(row.account_id)
        if req.account_id and _canonical_uuid(req.account_id) != account_id:
            raise HTTPException(status_code=404, detail=FORWARD_NOT_IN_MAILBOX)

        # 2. The files to carry.
        files = (await db.execute(text(
            """SELECT id, filename, size_bytes FROM email_attachments
                WHERE message_id = CAST(:mid AS uuid)
                ORDER BY created_at, filename"""
        ), {"mid": mid})).fetchall()
        if req.attachment_ids is not None:
            by_id = {str(f.id): f for f in files}
            wanted = [_canonical_uuid(a) or a for a in req.attachment_ids]
            missing = [a for a in wanted if a not in by_id]
            if missing:
                raise HTTPException(status_code=404, detail="A named file is not a file of this mail.")
            chosen = [by_id[a] for a in dict.fromkeys(wanted)]
        else:
            chosen = list(files) if req.include_attachments else []
        if chosen and str(row.provider or "").lower() == "imap":
            raise HTTPException(status_code=422, detail=_IMAP_FILES)

        # 3. The note and the signature of the mailbox, as on a send.
        from gateway.routes.email.signature import build_signed_bodies
        sig_row = (await db.execute(text(
            "SELECT signature FROM email_assistant_settings WHERE account_id = :aid"
        ), {"aid": account_id})).fetchone()
        note_text, note_html = build_signed_bodies(
            (sig_row.signature if sig_row else "") or "", req.note or "", None)
        if note_text.strip() and not (note_html or "").strip():
            note_html = _html.escape(note_text).replace("\n", "<br>")
        subject = forward_subject(row.subject)

        # ``drafting`` imports ``send``, so the 413 mapper comes in here too.
        from gateway.routes.email.automation.drafting import _mail_too_large

        async with provider_session(db, owner, account_id=account_id) as sess:
            native = bool(getattr(sess.provider, "forwards_natively", False))
            every_file = bool(chosen) and len(chosen) == len(files)

            # 4a. Outlook, with every file: Graph forwards at the server, so
            #     no file passes through here and no size cap of ours applies.
            if native and every_file:
                with _mail_too_large(), _provider_refusal():
                    sent_id = await sess.provider.forward_message(
                        row.provider_message_id, req.to, cc=req.cc, bcc=req.bcc,
                        comment=note_text, subject=subject,
                    )
                sent_names = [str(f.filename or "attachment") for f in chosen]
                total = sum(int(f.size_bytes or 0) for f in chosen)
                _log.info("email.forwarded", account_id=account_id, message_id=mid,
                          attachments=len(sent_names), bytes=total, native=True)
                return {"id": sent_id, "ok": True, "subject": subject,
                        "attachments": sent_names, "bytes": total}
            # Graph's forward carries every file or none of them, so a subset
            # on Outlook would leave files out of the member's sight.
            if native and chosen:
                raise HTTPException(status_code=422, detail=OUTLOOK_SUBSET)

            # 4b. Every other forward rebuilds the mail: the bytes of each file
            #     through the one owned fetch, then one send.
            attachments, total = await _fetch_files(db, chosen, user)

            original_text = row.body_text or ""
            original_html = row.body_html or None
            if not original_html or not original_text.strip():
                original_text, original_html = await _original_body(
                    sess, row, user, original_text, original_html)
            body_text, body_html = forward_bodies(
                note_text, note_html, forward_header(row),
                original_text or row.snippet or "", original_html)
            with _mail_too_large(), _provider_refusal():
                sent_id = await sess.provider.send_message(
                    to=req.to,
                    subject=subject,
                    body_text=body_text,
                    body_html=body_html,
                    cc=req.cc,
                    bcc=req.bcc,
                    reply_to_message_id=None,
                    attachments=attachments or None,
                    thread_id=None,
                )

    _log.info(
        "email.forwarded", account_id=account_id, message_id=mid,
        attachments=len(attachments), bytes=total, native=False,
    )
    return {
        "id": sent_id,
        "ok": True,
        "subject": subject,
        "attachments": [a["filename"] for a in attachments],
        "bytes": total,
    }


async def _fetch_files(
    db: Any, chosen: list[Any], user: UserContext,
) -> tuple[list[dict[str, Any]], int]:
    """The bytes of each chosen file, through the one owned fetch of a file.

    The stored sizes are checked against :data:`MAX_FORWARD_BYTES` before any
    fetch, and the fetched bytes after each one (413). A file with no bytes
    stops the forward with 422.
    """
    stored = sum(int(f.size_bytes or 0) for f in chosen)
    if stored > MAX_FORWARD_BYTES:
        raise _too_big(stored)
    attachments: list[dict[str, Any]] = []
    total = 0
    for f in chosen:
        with _provider_refusal():
            got = await _fetch_owned_attachment(db, str(f.id), user)
        if not got.content:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"The mail provider gave no bytes for the file "
                    f"'{str(got.row.filename or 'file')[:200]}'. It can be an "
                    "attached mail or a link to a cloud file, so nothing was "
                    "sent. Forward it without that file."
                ),
            )
        total += len(got.content)
        if total > MAX_FORWARD_BYTES:
            raise _too_big(total)
        attachments.append({
            "filename": got.row.filename or "attachment",
            "mime_type": got.row.mime_type or "application/octet-stream",
            "content": got.content,
        })
    return attachments, total


@contextmanager
def _provider_refusal() -> Iterator[None]:
    """Answer a failed provider call with a reason, never a bare 500.

    The detail names the status and Graph's or Google's own error code, and
    never the URL, because the text of an ``httpx`` error holds it. A 4xx of
    the provider is the provider refusing THIS mail: 422, or the provider's
    own 401, 404 or 429. A 5xx or a network failure is the provider being
    down: 502, because a 4xx would tell a caller to change a request that was
    right.
    """
    try:
        yield
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        code = _provider_error_code(exc.response)
        _log.warning("email.forward_refused", status=status, code=code)
        named = f" ({code})" if code else ""
        if status == 401:
            raise HTTPException(status_code=401, detail="Email account authentication failed") from None
        if status == 404:
            raise HTTPException(
                status_code=404,
                detail="The mail provider no longer holds this email, so nothing was sent.",
            ) from None
        if status == 429:
            raise HTTPException(
                status_code=429,
                detail="The mail provider asked for a pause. Nothing was sent. Try again later.",
                headers={"Retry-After": exc.response.headers.get("Retry-After", "60")},
            ) from None
        if 400 <= status < 500:
            raise HTTPException(
                status_code=422,
                detail=f"The mail provider refused the forward: HTTP {status}{named}. Nothing was sent.",
            ) from None
        raise HTTPException(
            status_code=502,
            detail=f"The mail provider failed: HTTP {status}{named}. Nothing was sent.",
        ) from None
    except httpx.TransportError as exc:
        _log.warning("email.forward_unreached", error=type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="The mail provider did not answer, so nothing was sent.",
        ) from None


def _provider_error_code(resp: httpx.Response) -> str:
    """The provider's own error code (Graph ``error.code``, Google
    ``error.status``), cut short, else ``""``. Never the message text."""
    try:
        err = resp.json().get("error")
    except Exception:
        return ""
    if isinstance(err, dict):
        code = err.get("code") if isinstance(err.get("code"), str) else err.get("status")
        return re.sub(r"[^A-Za-z0-9_.-]", "", str(code or ""))[:60]
    return ""


async def _original_body(
    sess: Any, row: Any, user: UserContext, text_body: str, html_body: str | None,
) -> tuple[str, str | None]:
    """The text and the HTML of the original, when the row lacks one.

    The cache of the reading pane first (``GET /email/messages/{id}/html``
    fills it), then one read of the body from the provider, in the session
    that sends. A failed read keeps what the row holds: a forward with the
    stored text is better than no forward.
    """
    if not html_body:
        from gateway.routes.email.transport.messages import _cached_html
        hit, cached = await _cached_html(user.organization_id, str(row.id))
        if hit and cached:
            html_body = cached
    if html_body and text_body.strip():
        return text_body, html_body
    try:
        full = await sess.provider.get_message_body(row.provider_message_id)
    except Exception as exc:  # the stored text still goes out
        _log.warning("email.forward_body_unread", message_id=str(row.id),
                     error=type(exc).__name__)
        return text_body, html_body
    return (
        text_body if text_body.strip() else (getattr(full, "body_text", "") or ""),
        html_body or (getattr(full, "body_html", None) or None),
    )
