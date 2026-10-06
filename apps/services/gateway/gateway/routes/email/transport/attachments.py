"""Transport · attachments — attachment download, the text of an attachment,
and the sandboxed image proxy.

The download route and the text route (WS-17 EM-T11) share ONE owned fetch,
:func:`_fetch_owned_attachment`: the ownership query, the tenant cache and the
provider dance. No second copy of the ownership query exists.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import json
import os
import re
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from acb_auth import UserContext, get_current_user
from acb_common import db_busy
from acb_common.tenant_redis import get_tenant_redis, key, organization_scope
from acb_skills.attachment_text import PAGE_UNREADABLE, SUPPORTED_SUFFIXES, Extracted
from acb_skills.attachment_tools import parse_bounded
from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from gateway.db import TenantUnbound
from gateway.routes.email.core import (
    ATTACHMENT_CACHE_TTL_SECS,
    _tenant_session,
    _log,
    provider_session,
    router,
)
from pydantic import BaseModel
from sqlalchemy import text

MAX_PROXY_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB


def _resolve_is_public(host: str) -> bool:
    """True only if every A/AAAA record for host is a public, routable IP.

    Blocks SSRF to loopback/private/link-local/metadata endpoints.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            return False
    return True


@router.get("/image-proxy")
async def image_proxy(
    url: str = Query(..., max_length=4096),
    user: UserContext = Depends(get_current_user),
):
    """Fetch a remote email image server-side and stream it back.

    Lets the reading pane show images without the sender's tracking pixel
    seeing the *user's* IP — only the gateway's IP is exposed.  Guarded
    against SSRF (scheme + private-IP checks) and size-capped.
    """
    # This proxy fetches arbitrary URLs server-side, so it must not be an open
    # relay: require an authenticated caller. (Direct-to-gateway requests resolve
    # to anonymous now that the header-trust bypass is closed.)
    if not user.email:
        raise HTTPException(status_code=401, detail="Unauthorized")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid image URL")
    if not await asyncio.to_thread(_resolve_is_public, parsed.hostname):
        raise HTTPException(status_code=400, detail="Blocked image host")

    try:
        # Follow redirects MANUALLY, re-validating each hop's host against the
        # public-IP guard. httpx's own follow_redirects only checks the first URL,
        # then happily jumps to an internal target (SSRF). Images legitimately
        # redirect via CDNs, so we can't just disable redirects here.
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=15.0
        ) as client:
            current = url
            resp = None
            for _ in range(4):  # initial request + up to 3 redirects
                resp = await client.get(
                    current,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Metorite image proxy)",
                        "Accept": "image/*",
                    },
                )
                if resp.is_redirect and resp.headers.get("location"):
                    nxt = urljoin(current, resp.headers["location"])
                    p = urlparse(nxt)
                    if p.scheme not in ("http", "https") or not p.hostname:
                        raise HTTPException(
                            status_code=400, detail="Invalid redirect")
                    if not await asyncio.to_thread(_resolve_is_public, p.hostname):
                        raise HTTPException(
                            status_code=400, detail="Blocked redirect host")
                    current = nxt
                    continue
                break
            else:
                raise HTTPException(status_code=400, detail="Too many redirects")
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=415, detail="Not an image")
            content = resp.content
            if len(content) > MAX_PROXY_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail="Image too large")
            return StreamingResponse(
                io.BytesIO(content),
                media_type=content_type,
                headers={
                    "Content-Length": str(len(content)),
                    "Cache-Control": "private, max-age=3600",
                },
            )
    except HTTPException:
        raise
    except Exception as exc:
        _log.warning("image_proxy.failed", error=str(exc)[:200])
        raise HTTPException(status_code=502, detail="Failed to fetch image")


# ── The one owned fetch of an attachment (WS-17 EM-T11) ─────────────────────


@dataclass(frozen=True)
class _OwnedAttachment:
    """The row and the bytes of one attachment that the caller owns.

    ``stopped`` holds what the caller's ``stop`` returned, and then
    ``content`` is empty: the helper read no cache and called no provider.
    """

    row: Any
    content: bytes = b""
    cached: bool = False
    stopped: Any = None


async def _fetch_owned_attachment(
    db: Any,
    attachment_id: str,
    user: UserContext,
    *,
    max_bytes: int | None = None,
    stop: Callable[[Any], Awaitable[Any]] | None = None,
) -> _OwnedAttachment:
    """The row and the bytes of an attachment of the caller's own mail.

    The ONE fetch of the download route and the text route (EM-T11,
    ``email_app_master_plan.md`` §10.4.12). It keeps this order:

    1. The ownership query runs first, and a row of another member is 404.
       A UUID in a spelling that :func:`_canonical_uuid` reads goes into the
       query in its canonical form.
    2. *stop* sees the owned row. A truthy answer ends the fetch here, with
       no cache read and no provider call.
    3. *max_bytes* refuses a row whose stored size is larger, with 413,
       before the cache and the provider.
    4. The cache: only with an organization in the session, inside
       ``organization_scope``, on the binary pool, keyed by the id of the ROW.
       So two spellings of one id share one entry. A Redis failure turns the
       cache off for this request.
    5. The provider fetch, through ``provider_session`` inside the caller's
       tenant session, with the default ``require_auth``.
    6. A cache write, only for bytes that are not empty and not over
       *max_bytes*. A file that the text route refuses takes no cache space.

    ⚠️ **The caller opens the tenant session, OUTSIDE its own ``try``**
    (EM-T11 review round 1). So ``TenantUnbound`` and a refused connect reach
    the handlers of ``main.py`` (403 or 503, and 503 "busy"), as they did
    before EM-T11. Inside a route's ``except Exception`` they became a 500 or
    a 502. Fence: ``TestTheSessionFaultsReachTheHandlers``.

    ⚠️ The session holds one pooled connection, in an open transaction,
    across ``authenticate()`` and the provider download. The download route
    on main had the same shape. EM-T4a-4 owns the split (§10.4.12, the known
    limits).
    """
    aid = _canonical_uuid(attachment_id) or attachment_id
    # Look up attachment and verify user owns the parent message
    result = await db.execute(
        text(
            """SELECT ea.id, ea.filename, ea.mime_type, ea.size_bytes,
                      ea.provider_attachment_id, ea.storage_path,
                      em.provider_message_id, em.account_id, p.provider
               FROM email_attachments ea
               JOIN email_messages em ON ea.message_id = em.id
               JOIN email_accounts p ON em.account_id = p.id
               WHERE ea.id = :aid AND p.user_id = :user_id"""
        ),
        {"aid": aid, "user_id": user.email or "anonymous"},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Attachment not found")

    stopped = await stop(row) if stop is not None else None
    if stopped:
        return _OwnedAttachment(row=row, stopped=stopped)
    if max_bytes is not None and (row.size_bytes or 0) > max_bytes:
        raise HTTPException(status_code=413, detail=_too_large(max_bytes))

    # ── Ownership confirmed — NOW it's safe to serve from the Redis cache.
    #    Reading the cache before this check was an IDOR: any caller who knew
    #    an attachment_id could pull another user's cached bytes. ──
    #    The key carries the tenant (MT-1e, EM-T2b): the session's org is
    #    bound for each Redis call, so org B never reads org A's entry for
    #    the same attachment id. No org in the session → no cache at all,
    #    and no Redis call. The binary pool returns bytes unchanged.
    #    The key holds the id of the ROW, never the path text (review round
    #    1): `uuid.UUID` and asyncpg read an upper-case id and extra hyphens,
    #    and each spelling used to write its own 15 MB entry.
    org_id = user.organization_id
    use_cache = bool(org_id)
    cached: bytes | None = None
    if use_cache:
        try:
            with organization_scope(org_id):
                cached = await get_tenant_redis(binary=True).get(
                    key("email-att", str(row.id))
                )
        except Exception:
            use_cache = False  # fall through to provider fetch
    if cached:
        return _OwnedAttachment(row=row, content=cached, cached=True)

    # Fetch through the ONE provider dance. This path used to instantiate
    # the provider raw — never authenticating (an expired access token just
    # 401'd) and never persisting a rotated refresh token (silently dropped,
    # so the NEXT request re-authed from a stale token).
    async with provider_session(
        db, user.email or "anonymous", account_id=str(row.account_id),
    ) as sess:
        content = await sess.provider.get_attachment(
            row.provider_message_id, row.provider_attachment_id
        )

    # ── Store in Redis cache ── never empty bytes: an Outlook item or
    #    reference attachment gives b"", and a cached b"" reads as a miss.
    #    Never bytes over *max_bytes* either: the stored size can be NULL or
    #    wrong, and the caller refuses such a file (review round 1).
    fits = max_bytes is None or len(content or b"") <= max_bytes
    if use_cache and content and fits:
        try:
            with organization_scope(org_id):
                await get_tenant_redis(binary=True).setex(
                    key("email-att", str(row.id)),
                    ATTACHMENT_CACHE_TTL_SECS,
                    content,
                )
        except Exception:
            pass

    return _OwnedAttachment(row=row, content=content or b"")


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(
    attachment_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Proxy download an email attachment, streaming from the provider.

    Checks Redis cache first (TTL 1 hour) to avoid redundant provider API
    calls for attachments downloaded multiple times. The ownership check, the
    cache and the provider fetch are :func:`_fetch_owned_attachment`.
    """
    # ⚠️ The session opens OUTSIDE the try, as it did before EM-T11 (review
    # round 1). `TenantUnbound` and a refused connect then reach main.py's
    # handlers, never the 500 below.
    async with _tenant_session() as db:
        try:
            got = await _fetch_owned_attachment(db, attachment_id, user)
        except HTTPException:
            raise
        except Exception as exc:
            _log.error("download_attachment.failed", aid=attachment_id, error=str(exc)[:200])
            raise HTTPException(status_code=500, detail="Failed to download attachment")
    row = got.row

    # Sanitise the filename for the Content-Disposition header — strip quotes
    # / CR / LF so an attacker-controlled attachment name can't break out of
    # the quoted value (header-injection / filename-spoofing).
    safe_name = (
        (row.filename or "attachment")
        .replace('"', "'").replace("\n", " ").replace("\r", " ")
    )
    # A hit keeps the octet-stream default, and a miss sends the row's type
    # as it is, exactly as the route did before EM-T11.
    media_type = (row.mime_type or "application/octet-stream") if got.cached else row.mime_type
    return StreamingResponse(
        io.BytesIO(got.content),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "Content-Length": str(len(got.content)),
            "X-Cache": "HIT" if got.cached else "MISS",
        },
    )


# ── The text of an attachment, for the email assistant (WS-17 EM-T11) ───────

#: The bytes that the text route reads at most. Above it the route answers 413.
MAX_TEXT_INPUT_BYTES = 15 * 1024 * 1024
#: The characters that the text route returns at most. ``truncated`` says when
#: it cut. The shared reader's own caps bind first (``attachment_text``).
MAX_TEXT_OUTPUT_CHARS = 20_000

#: The type of a file whose name has no suffix. A name WITH a suffix decides by
#: the suffix alone, so a ``.exe`` that claims ``text/plain`` stays unread and a
#: ``.txt`` that claims ``image/png`` reads as text.
_SUFFIX_OF_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
}
_IMAGE_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic",
})
#: A ``[Page N]`` head line of ``attachment_text._page_text``. A PDF whose
#: lines are all heads has no text layer.
_PAGE_HEAD = re.compile(r"^\[Page \d+\]$")
#: A UUID as hex digits and hyphens only, the forms that asyncpg also reads.
#: ``uuid.UUID`` also takes braces and ``urn:uuid:``, and asyncpg refuses both.
_UUID_TEXT = re.compile(r"[0-9A-Fa-f-]+")
#: The database libraries. An error from one of them is a fault of the
#: database, and the text route never blames the mail provider for it.
_DB_LIBRARIES = frozenset({"sqlalchemy", "asyncpg", "psycopg", "psycopg_pool"})

_IMAP_REASON = (
    "I cannot read a file of an IMAP mailbox yet. Ask the member to open it "
    "in their mail app."
)
_ATTACHED_MAIL_REASON = (
    "This attachment is a mail, and I cannot read an attached mail yet. Ask "
    "the member to open it in the Email app."
)
_IMAGE_REASON = (
    "This attachment is an image. I cannot read the text of an image, "
    "because I have no OCR."
)
_EMPTY_REASON = (
    "The mail provider gave no bytes for this attachment. It can be an "
    "attached mail or a link to a cloud file, and I cannot read those."
)
_NO_TEXT_REASON = (
    "This file holds no text that I can read. A scanned page is an image, "
    "and I cannot read the text of an image."
)
_NO_PAGE_REASON = (
    "I could not read the text of any page of this PDF. The file can be "
    "damaged, or its pages use a form that I cannot read."
)
_BUSY_REASON = (
    "I am reading another file for you now. Try again when that read is done."
)


class AttachmentTextModel(BaseModel):
    """The answer of ``GET /email/attachments/{id}/text``.

    ``kind`` is the type that the shared reader read (``pdf``, ``docx``,
    ``txt``, ``md`` or ``csv``), or one of three answers with no text:
    ``unsupported`` (a type or a source that the slice does not read),
    ``no_text`` (an image, or a file with no text layer) and ``unreadable``
    (the reader refused the file: a password, a limit, a broken file, no page
    that parsed, or a busy reader). Each answer with no text carries
    ``reason``, a sentence for the member. ``chars`` counts the text that the
    reader got, before the cut.
    """

    filename: str
    mime_type: str
    kind: str
    text: str = ""
    truncated: bool = False
    chars: int = 0
    reason: str | None = None


def _too_large(limit: int) -> str:
    return f"This file is larger than {limit // (1024 * 1024)} MB, so I did not read it."


def _mime_base(mime: str | None) -> str:
    return (mime or "").split(";", 1)[0].strip().lower()


def _name_suffix(filename: str | None) -> str:
    """The suffix of the file name, lower case, else ``""``."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    return os.path.splitext(name)[1].lower()


def _text_suffix(filename: str | None, mime: str | None) -> str:
    """The suffix of the file name, lower case. With none, the suffix of the
    type, else ``""``."""
    return _name_suffix(filename) or _SUFFIX_OF_MIME.get(_mime_base(mime), "")


def _no_fetch(row: Any) -> tuple[str, str] | None:
    """``(kind, reason)`` when the route answers without the bytes, else None.

    The owned row decides it, before the cache and the provider: an IMAP
    mailbox (its ids are ordinals and its fetch gives an encoded MIME part),
    an attached mail, an image, and a type that the shared reader does not
    read. Never a guess: an unknown type is never decoded as text.

    A name with a suffix decides by the suffix alone (departure 2), so an
    image type does not override ``.txt`` or ``.pdf`` (review round 1). The
    one type that wins over a suffix is ``message/rfc822``. It only refuses,
    and Outlook names an attached mail after its subject, which can end in
    ``.pdf``.
    """
    named = _name_suffix(row.filename)
    mime = _mime_base(row.mime_type)
    suffix = named or _SUFFIX_OF_MIME.get(mime, "")
    if str(row.provider or "").lower() == "imap":
        return "unsupported", _IMAP_REASON
    if suffix == ".eml" or mime == "message/rfc822":
        return "unsupported", _ATTACHED_MAIL_REASON
    if suffix in _IMAGE_SUFFIXES or (not named and mime.startswith("image/")):
        return "no_text", _IMAGE_REASON
    if suffix not in SUPPORTED_SUFFIXES:
        return "unsupported", (
            f"I cannot read a {suffix or 'file without a type'} file. "
            "I read .pdf, .docx, .txt, .md and .csv files."
        )
    return None


def _text_state(got: Extracted) -> str:
    """``"words"``, ``"none"`` (no text) or ``"unread"`` (no page parsed).

    A line of a PDF is a ``[Page N]`` head, the reader's placeholder for a page
    that did not parse (``attachment_text.PAGE_UNREADABLE``), or words. A PDF
    of placeholders only is ``"unread"``, never words (review round 1).
    """
    if got.kind != "pdf":
        return "words" if got.text.strip() else "none"
    lines = [line.strip() for line in got.text.splitlines()]
    body = [line for line in lines if line and not _PAGE_HEAD.match(line)]
    if any(line != PAGE_UNREADABLE for line in body):
        return "words"
    return "unread" if body else "none"


def _canonical_uuid(value: str) -> str | None:
    """The canonical form of a UUID, else ``None``.

    Strict: hex digits and hyphens only, 32 digits. ``uuid.UUID`` also takes
    braces and ``urn:uuid:``, which asyncpg refuses, so the text route answers
    404 for such an id (review round 1).
    """
    raw = str(value or "")
    if not _UUID_TEXT.fullmatch(raw) or len(raw.replace("-", "")) != 32:
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return None


def _is_database_fault(exc: BaseException) -> bool:
    """True for a fault of the database, never of the mail provider.

    ``TenantUnbound`` and a refused connect (``acb_common.db_busy``) keep the
    answers of ``main.py``: 403 or 503, and 503 "busy". Any other error of a
    database library is the generic 500. Each is ours, not the provider's.
    """
    if isinstance(exc, TenantUnbound) or db_busy.is_db_unavailable(exc):
        return True
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen and len(seen) < 8:
        seen.add(id(cur))
        if type(cur).__module__.split(".", 1)[0] in _DB_LIBRARIES:
            return True
        cur = cur.__cause__
    return False


# ── The text cache and one read for each member (review round 1) ────────────

#: The members with a text read in flight, as (organization, email).
#: ⚠️ In memory, and that is enough: the gateway runs as ONE process (one
#: uvicorn with no ``--workers``, ``deploy/hostinger/acb-gateway.service``),
#: and the parse pool of ``attachment_tools`` is one for each process too. A
#: second process would need a lock in Redis.
_READING: set[tuple[str, str]] = set()


class _ReadHold:
    """One member's right to read a file now, at most one at a time.

    The parse pool has two slots for the whole process. Without this, one
    member who calls the route in a loop holds both slots, and the
    ``read_attachment`` of every other member answers busy. ``take`` runs on
    the event loop with no ``await`` between the test and the add, so two
    requests of one member cannot both take it.
    """

    def __init__(self, user: UserContext) -> None:
        self._key = (str(user.organization_id or ""), (user.email or "").strip().lower())
        self._held = False

    def take(self) -> bool:
        if self._key in _READING:
            return False
        _READING.add(self._key)
        self._held = True
        return True

    def release(self) -> None:
        if self._held:
            _READING.discard(self._key)
            self._held = False


#: The fields of a cached answer. The file name and the type come from the
#: owned row at each read.
_CACHED_FIELDS = ("kind", "text", "truncated", "chars", "reason")


def _base(row: Any) -> dict[str, str]:
    return {
        "filename": row.filename or "attachment",
        "mime_type": row.mime_type or "application/octet-stream",
    }


async def _cached_text(org_id: str | None, row: Any) -> AttachmentTextModel | None:
    """The answer that an earlier read of this row cached, else ``None``.

    Only after the ownership check, only with an organization, and keyed by
    the id of the row in tenant Redis. A Redis failure or a bad entry is a
    miss.
    """
    if not org_id:
        return None
    try:
        with organization_scope(org_id):
            raw = await get_tenant_redis().get(key("email-att-text", str(row.id)))
        if not raw:
            return None
        data = json.loads(raw)
        return AttachmentTextModel(**_base(row), **{f: data[f] for f in _CACHED_FIELDS})
    except Exception:
        return None


async def _remember_text(org_id: str | None, row: Any, answer: AttachmentTextModel) -> None:
    """Cache *answer* for the TTL of the bytes cache. Best effort."""
    if not org_id:
        return
    try:
        payload = json.dumps({f: getattr(answer, f) for f in _CACHED_FIELDS})
        with organization_scope(org_id):
            await get_tenant_redis().setex(
                key("email-att-text", str(row.id)), ATTACHMENT_CACHE_TTL_SECS, payload,
            )
    except Exception:
        pass


@router.get("/attachments/{attachment_id}/text", response_model=AttachmentTextModel)
async def attachment_text(
    attachment_id: str,
    user: UserContext = Depends(get_current_user),
) -> AttachmentTextModel:
    """The text of one attachment of the caller's own mail (WS-17 EM-T11).

    The email assistant's ``read_email_attachment`` calls it. The ownership
    check, the cache and the provider fetch are the download route's
    (:func:`_fetch_owned_attachment`), so an attachment of another member is
    404. A parsed answer is cached for the organization, by the id of the
    row, so a second read parses nothing. A member runs one read at a time.
    The parse runs on the shared bounded pool
    (``acb_skills.attachment_tools.parse_bounded``), after the database
    session has closed. Spec: ``email_app_master_plan.md`` §10.4.12.
    """
    aid = _canonical_uuid(attachment_id)
    if aid is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    org_id = user.organization_id
    hold = _ReadHold(user)

    async def _answer_first(row: Any) -> AttachmentTextModel | None:
        """An answer with no fetch: a refused type, the cache, or busy."""
        early = _no_fetch(row)
        if early is not None:
            return AttachmentTextModel(**_base(row), kind=early[0], reason=early[1])
        cached = await _cached_text(org_id, row)
        if cached is not None:
            return cached
        if not hold.take():
            return AttachmentTextModel(**_base(row), kind="unreadable", reason=_BUSY_REASON)
        return None

    try:
        # ⚠️ The session opens OUTSIDE the try (review round 1), so
        # `TenantUnbound` and a refused connect reach main.py's handlers.
        async with _tenant_session() as db:
            try:
                got = await _fetch_owned_attachment(
                    db, aid, user, max_bytes=MAX_TEXT_INPUT_BYTES, stop=_answer_first,
                )
            except HTTPException:
                raise
            except Exception as exc:
                if _is_database_fault(exc):
                    raise
                _log.error("attachment_text.fetch_failed", aid=aid, error=str(exc)[:200])
                raise HTTPException(
                    status_code=502, detail="The mail provider did not give the file.",
                ) from None
        if got.stopped:
            return got.stopped
        row = got.row
        base = _base(row)
        if not got.content:
            return AttachmentTextModel(**base, kind="unsupported", reason=_EMPTY_REASON)
        if len(got.content) > MAX_TEXT_INPUT_BYTES:
            raise HTTPException(status_code=413, detail=_too_large(MAX_TEXT_INPUT_BYTES))

        suffix = _text_suffix(row.filename, row.mime_type)
        parsed = await parse_bounded(got.content, suffix)
        if isinstance(parsed, str):
            # Not cached: a busy pool and a deadline pass with time.
            _log.info("attachment_text.refused", aid=aid, kind=suffix, size=len(got.content))
            return AttachmentTextModel(**base, kind="unreadable", reason=parsed)
        state = _text_state(parsed)
        if state == "unread":
            answer = AttachmentTextModel(**base, kind="unreadable", reason=_NO_PAGE_REASON)
        elif state == "none":
            answer = AttachmentTextModel(**base, kind="no_text", reason=_NO_TEXT_REASON)
        else:
            full = parsed.text
            _log.info("attachment_text.read", aid=aid, kind=parsed.kind,
                      size=len(got.content), chars=len(full), stopped=parsed.stopped)
            answer = AttachmentTextModel(
                **base,
                kind=parsed.kind,
                text=full[:MAX_TEXT_OUTPUT_CHARS],
                truncated=len(full) > MAX_TEXT_OUTPUT_CHARS or parsed.stopped,
                chars=len(full),
            )
        await _remember_text(org_id, row, answer)
        return answer
    finally:
        hold.release()
