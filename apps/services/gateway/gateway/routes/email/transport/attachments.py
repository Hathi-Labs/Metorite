"""Transport · attachments — attachment download, the text of an attachment,
and the sandboxed image proxy.

The download route and the text route (WS-17 EM-T11) share ONE owned fetch,
:func:`_fetch_owned_attachment`: the ownership query, the tenant cache and the
provider dance. No second copy of the ownership query exists.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from acb_auth import UserContext, get_current_user
from acb_common import db_busy
from acb_common.db import mark_background
from acb_common.public_address import host_is_public
from acb_common.tenant_redis import (
    BINARY_POOL_MAX_CONNECTIONS,
    get_tenant_redis,
    key,
    organization_scope,
)
from acb_skills import attachment_ocr
from acb_skills.attachment_text import (
    SUPPORTED_SUFFIXES,
    ReaderBusy,
    text_state,
    unsupported_sentence,
)
from acb_skills.attachment_tools import parse_bounded
from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from gateway.db import TenantUnbound
from gateway.routes.email.core import (
    ATTACHMENT_CACHE_TTL_SECS,
    _log,
    _tenant_session,
    provider_session,
    router,
)
from gateway.routes.email.mailbox_access import mailbox_predicate
from pydantic import BaseModel
from sqlalchemy import text

MAX_PROXY_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB

#: How many binary Redis calls run at once in this process. The binary pool
#: of ``tenant_redis`` holds ``BINARY_POOL_MAX_CONNECTIONS`` and raises when it
#: is full, with no wait. Both of its users, the file cache below and the
#: inline pictures (``inline_images.py``), queue on this gate, one below the
#: pool, so a burst waits instead of failing (security review 2026-10-13,
#: P2-1 and P3-c). The pool itself stays as it is.
BINARY_REDIS_GATE_SIZE = max(1, BINARY_POOL_MAX_CONNECTIONS - 1)

#: One gate for each event loop. An ``asyncio.Semaphore`` binds to the loop
#: that first waits on it, so a gate kept across loops would break.
_BINARY_GATES: dict[int, asyncio.Semaphore] = {}


def _binary_redis_gate() -> asyncio.Semaphore:
    """The gate of each call on the binary Redis pool."""
    loop = id(asyncio.get_running_loop())
    gate = _BINARY_GATES.get(loop)
    if gate is None:
        _BINARY_GATES.clear()
        gate = _BINARY_GATES[loop] = asyncio.Semaphore(BINARY_REDIS_GATE_SIZE)
    return gate


def _resolve_is_public(host: str) -> bool:
    """True only if every A/AAAA record for host is a public, routable IP.

    Blocks SSRF to loopback/private/link-local/metadata endpoints. The rules
    live in ``acb_common.public_address``, the one shared test.
    """
    return host_is_public(host)


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
               WHERE ea.id = :aid AND """
            + mailbox_predicate("p", uid="user_id", level="read")
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
            async with _binary_redis_gate():
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
            async with _binary_redis_gate():
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
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
    "text/html": ".html",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/yaml": ".yaml",
    "text/yaml": ".yaml",
    # The older Office kinds map too, so they get the sentence that says how
    # to save them again (``attachment_text.LEGACY_OFFICE``).
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
}
_IMAGE_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic",
})
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

    ``kind`` is the type that the shared reader read (a key of
    ``acb_skills.attachment_text.KIND_NAMES``, such as ``pdf``, ``docx`` or
    ``pptx``), or one of four answers with no text:
    ``unsupported`` (a type or a source that the slice does not read),
    ``no_text`` (an image, or a file with no text layer) and ``unreadable``
    (the reader refused the file: a password, a limit, a broken file, no page
    that parsed, or a busy reader), and ``busy`` (the OCR slot was in use,
    WS-67 OA-3: try again). Each answer with no text carries
    ``reason``, a sentence for the member. ``chars`` counts the text that the
    reader got, before the cut. ``offset`` is the character where ``text``
    starts, and ``next_offset`` is the one where the next part starts, or
    ``None`` when ``text`` reaches the end.
    """

    filename: str
    mime_type: str
    kind: str
    text: str = ""
    truncated: bool = False
    chars: int = 0
    reason: str | None = None
    offset: int = 0
    next_offset: int | None = None


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


def _no_fetch(row: Any, *, ocr: bool = True) -> tuple[str, str] | None:
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

    *ocr* ``False`` answers as with ``ATTACHMENT_OCR`` off (fix round 2): the
    reply drafter reads with no OCR.
    """
    use_ocr = ocr and attachment_ocr.ocr_enabled()
    named = _name_suffix(row.filename)
    mime = _mime_base(row.mime_type)
    suffix = named or _SUFFIX_OF_MIME.get(mime, "")
    if str(row.provider or "").lower() == "imap":
        return "unsupported", _IMAP_REASON
    if suffix == ".eml" or mime == "message/rfc822":
        return "unsupported", _ATTACHED_MAIL_REASON
    if suffix in _IMAGE_SUFFIXES or (not named and mime.startswith("image/")):
        # WS-67 OA-3: with ATTACHMENT_OCR on, a .png, .jpg or .jpeg image
        # is fetched and read by OCR. Any other image kind says so.
        if use_ocr and attachment_ocr.image_suffix(named, mime):
            return None
        if use_ocr:
            return "no_text", attachment_ocr.OTHER_IMAGE_SENTENCE
        return "no_text", _IMAGE_REASON
    if suffix not in SUPPORTED_SUFFIXES:
        return "unsupported", unsupported_sentence(suffix)
    return None


def _cache_namespace(*, ocr: bool = True) -> str:
    """The tenant Redis namespace of the text cache.

    With ``ATTACHMENT_OCR`` on (WS-67 OA-3) the answers of an OCR read live
    under their own name. So a "no text" answer that was cached before the
    flag turned on does not hide the OCR text for the hour of its TTL. A read
    with *ocr* ``False`` uses the old name (fix round 2), so it never writes a
    "no text" answer where an OCR read looks, and never reads an OCR answer.
    """
    if ocr and attachment_ocr.ocr_enabled():
        return "email-att-text-ocr"
    return "email-att-text"


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
#: owned row at each read. The cache holds the WHOLE text that the reader got,
#: so a read at an ``offset`` parses nothing. ``truncated`` in the cache says
#: only that the reader stopped early. An entry without ``"whole": true`` is
#: from before ``offset`` (it holds the first part only), and reads as a miss.
_CACHED_FIELDS = ("kind", "text", "truncated", "chars", "reason")

#: The most characters of one file that the cache holds and that an offset
#: can reach (review of #1220, P2). A 2,000,000-character entry can take
#: about 12 MB of Redis after JSON escapes. 200,000 is about 60 pages.
MAX_CACHED_TEXT_CHARS = 200_000


def _text_part(answer: AttachmentTextModel, offset: int) -> AttachmentTextModel:
    """The part of a whole-text *answer* that starts at character *offset*.

    At most ``MAX_TEXT_OUTPUT_CHARS`` characters. ``truncated`` is true when
    text follows this part, or when the reader stopped early. An answer with
    no text comes back as it is. Fence: tests/unit/test_email_attachment_text.py.
    """
    full = answer.text
    if not full:
        return answer
    start = min(max(0, int(offset or 0)), len(full))
    end = start + MAX_TEXT_OUTPUT_CHARS
    more = end < len(full)
    return answer.model_copy(update={
        "text": full[start:end],
        "truncated": more or answer.truncated,
        "offset": start,
        "next_offset": end if more else None,
    })


def _base(row: Any) -> dict[str, str]:
    return {
        "filename": row.filename or "attachment",
        "mime_type": row.mime_type or "application/octet-stream",
    }


async def _cached_text(
    org_id: str | None, row: Any, namespace: str = "email-att-text",
) -> AttachmentTextModel | None:
    """The answer that an earlier read of this row cached, else ``None``.

    Only after the ownership check, only with an organization, and keyed by
    the id of the row in tenant Redis, under *namespace*
    (:func:`_cache_namespace`). A Redis failure or a bad entry is a miss.
    """
    if not org_id:
        return None
    try:
        with organization_scope(org_id):
            raw = await get_tenant_redis().get(key(namespace, str(row.id)))
        if not raw:
            return None
        data = json.loads(raw)
        if data.get("whole") is not True:
            return None
        return AttachmentTextModel(**_base(row), **{f: data[f] for f in _CACHED_FIELDS})
    except Exception:
        return None


async def _remember_text(
    org_id: str | None, row: Any, answer: AttachmentTextModel,
    namespace: str = "email-att-text",
) -> None:
    """Cache *answer* under *namespace* for the TTL of the bytes cache. Best
    effort."""
    if not org_id:
        return
    try:
        payload = json.dumps(
            {**{f: getattr(answer, f) for f in _CACHED_FIELDS}, "whole": True})
        with organization_scope(org_id):
            await get_tenant_redis().setex(
                key(namespace, str(row.id)), ATTACHMENT_CACHE_TTL_SECS, payload,
            )
    except Exception:
        pass


#: The seconds that the text route may take for a read with OCR (WS-67 OA-3
#: fix round 2). ``read_email_attachment`` waits 60 s for the route, and the
#: rest is for the database query, the Redis cache and the reply. The OCR
#: child, its slot wait and its reap all end before this deadline.
TEXT_ROUTE_BUDGET_S = 55.0
#: The seconds that the route waits for the owned fetch (the query, the bytes
#: cache and the mail provider) in a read with OCR. The fetch itself goes on
#: after that and fills the bytes cache, so the next read is fast (fix round
#: 3, P2-2). A read with no OCR keeps its old, unbounded fetch, so the flag
#: off changes nothing. 30 s and the 22 s of the text parse still end before
#: the deadline of :data:`TEXT_ROUTE_BUDGET_S`.
FETCH_TIMEOUT_S = 30.0

#: The fetches that may run on after their route answered 504 (fix round 4).
#: Each one holds a connection of the background lane (H-390). Above this
#: count, the route cancels its fetch at its timeout, as in round 2.
MAX_BACKGROUND_FETCHES = 2
#: The longest one fetch may run in total, also after its route answered.
#: httpx bounds each read of the provider, not the whole download.
BACKGROUND_FETCH_LIMIT_S = 120.0


@dataclass
class _FetchJob:
    """One running fetch of a read with OCR, and the hold of its member.

    ``detached`` is set when the route ends while the fetch still runs. The
    done callback then releases the hold, so the member's next read answers
    busy until the fetch ends (fix round 4).
    """

    task: asyncio.Task[Any]
    hold: Any
    detached: bool = False


#: The running fetch of each (organization, member, attachment id). A retry
#: of the same read joins it and starts no second fetch. The key holds the
#: member, because each member proves ownership with the query of their own
#: fetch, and a join must never hand bytes to another member.
_RUNNING_FETCHES: dict[tuple[str, str, str], _FetchJob] = {}
#: The fetches that run on after their route answered, at most
#: :data:`MAX_BACKGROUND_FETCHES`. Each task stays here until it ends, so
#: nothing collects it half done.
_BACKGROUND_FETCHES: set[asyncio.Task[Any]] = set()


def _start_fetch(key: tuple[str, str, str], fetch: Awaitable[Any], hold: Any) -> _FetchJob:
    """Start *fetch* as the one running fetch of *key*."""
    task = asyncio.ensure_future(fetch)
    job = _FetchJob(task=task, hold=hold)
    _RUNNING_FETCHES[key] = job

    def _done(done: asyncio.Task[Any]) -> None:
        if _RUNNING_FETCHES.get(key) is job:
            del _RUNNING_FETCHES[key]
        _BACKGROUND_FETCHES.discard(done)
        if job.detached:
            job.hold.release()
        if not done.cancelled() and done.exception() is not None:
            _log.info("attachment_text.fetch_ended_badly",
                      error=type(done.exception()).__name__)

    task.add_done_callback(_done)
    return job


def _keep_or_cancel(job: _FetchJob) -> None:
    """At the route's fetch timeout: keep the fetch running under the cap,
    so that it fills the bytes cache, or cancel it above the cap."""
    if job.task.done():
        return  # the fetch itself ran out of time (BACKGROUND_FETCH_LIMIT_S)
    if len(_BACKGROUND_FETCHES) < MAX_BACKGROUND_FETCHES:
        _BACKGROUND_FETCHES.add(job.task)
        return
    job.task.cancel()
    _log.info("attachment_text.fetch_cancelled", reason="background_cap")


def _route_reads_with_ocr() -> bool:
    """The route's own answer for ``ocr``: always ``True``.

    A dependency with no parameter adds no query parameter. So no request
    can turn OCR off or on, and only a caller in the gateway that calls
    :func:`attachment_text` itself can pass ``ocr=False``.
    """
    return True


@router.get("/attachments/{attachment_id}/text", response_model=AttachmentTextModel)
async def attachment_text(
    attachment_id: str,
    user: UserContext = Depends(get_current_user),
    offset: int = 0,
    ocr: bool = Depends(_route_reads_with_ocr),
) -> AttachmentTextModel:
    """The text of one attachment of the caller's own mail (WS-17 EM-T11).

    ``offset`` (a query parameter, in characters) picks the part of the text
    to return, ``MAX_TEXT_OUTPUT_CHARS`` at most. The answer's
    ``next_offset`` names the next part. The cache holds the whole text, so
    a read of a later part parses nothing.

    The email assistant's ``read_email_attachment`` calls it. The ownership
    check, the cache and the provider fetch are the download route's
    (:func:`_fetch_owned_attachment`), so an attachment of another member is
    404. A parsed answer is cached for the organization, by the id of the
    row, so a second read parses nothing. A member runs one read at a time.
    The parse runs on the shared bounded pool
    (``acb_skills.attachment_tools.parse_bounded``), after the database
    session has closed. Spec: ``email_app_master_plan.md`` §10.4.12.

    With ``ATTACHMENT_OCR`` on, the route reads with OCR (WS-67 OA-3). It
    waits :data:`FETCH_TIMEOUT_S` for the fetch and then answers 504, while
    the fetch goes on in its own session and fills the bytes cache (fix
    round 3). At most :data:`MAX_BACKGROUND_FETCHES` go on, in the background
    lane. A retry of the same read joins the running fetch, and the member's
    hold stays taken until the fetch ends (fix round 4). OCR gets a deadline
    of :data:`TEXT_ROUTE_BUDGET_S` from the
    start. A busy OCR slot answers
    ``kind="busy"``. *ocr* ``False`` reads as with the flag off (fix round 2):
    no OCR child, the flag-off cache namespace, the flag-off answers and no
    time bound on the fetch. The reply drafter (``draft_files.py``) passes
    it, because it reads up to six files in a budget of 8 to 20 s.
    """
    aid = _canonical_uuid(attachment_id)
    if aid is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    ocr = ocr is not False  # a direct call that passes nothing reads as the route
    use_ocr = ocr and attachment_ocr.ocr_enabled()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + TEXT_ROUTE_BUDGET_S if use_ocr else None
    namespace = _cache_namespace(ocr=ocr)
    org_id = user.organization_id
    hold = _ReadHold(user)
    #: The fetch that this request started, if any (fix round 4).
    owned: list[_FetchJob] = []

    async def _answer_first(row: Any) -> AttachmentTextModel | None:
        """An answer with no fetch: a refused type, the cache, or busy."""
        early = _no_fetch(row, ocr=ocr)
        if early is not None:
            return AttachmentTextModel(**_base(row), kind=early[0], reason=early[1])
        cached = await _cached_text(org_id, row, namespace)
        if cached is not None:
            return cached
        if not hold.take():
            return AttachmentTextModel(**_base(row), kind="unreadable", reason=_BUSY_REASON)
        return None

    async def _fetch_alone() -> Any:
        """The owned fetch in a tenant session of its own (fix rounds 3, 4).

        It runs as a task that can outlive the route, so the session must be
        its own: the route closes its session when it answers 504. It marks
        itself background first (H-390), so it holds a connection of the
        background lane and never one that members need. It ends after
        :data:`BACKGROUND_FETCH_LIMIT_S` in total. ``TenantUnbound`` and a
        refused connect still reach main.py's handlers, because
        ``_fetch_for_text`` raises each fault of the database as it is.
        """
        mark_background()
        async with _tenant_session() as own:
            return await asyncio.wait_for(
                _fetch_owned_attachment(
                    own, aid, user, max_bytes=MAX_TEXT_INPUT_BYTES, stop=_answer_first,
                ),
                BACKGROUND_FETCH_LIMIT_S,
            )

    try:
        if use_ocr:
            # P2-2: the route waits FETCH_TIMEOUT_S, and the fetch may go on
            # and end in the bytes cache. A retry of the same read joins the
            # running fetch (fix round 4).
            key = (str(org_id or ""), (user.email or "").strip().lower(), aid)
            got = await _ocr_fetch(key, _fetch_alone, hold, aid, owned)
        else:
            # ⚠️ The session opens OUTSIDE the try (review round 1), so
            # `TenantUnbound` and a refused connect reach main.py's handlers.
            async with _tenant_session() as db:
                got = await _fetch_for_text(
                    _fetch_owned_attachment(
                        db, aid, user, max_bytes=MAX_TEXT_INPUT_BYTES, stop=_answer_first,
                    ),
                    aid, None,
                )
        if got.stopped:
            return _text_part(got.stopped, offset)
        row = got.row
        base = _base(row)
        if not got.content:
            return AttachmentTextModel(**base, kind="unsupported", reason=_EMPTY_REASON)
        if len(got.content) > MAX_TEXT_INPUT_BYTES:
            raise HTTPException(status_code=413, detail=_too_large(MAX_TEXT_INPUT_BYTES))

        suffix = _text_suffix(row.filename, row.mime_type)
        options: dict[str, Any] = {}
        if use_ocr:
            # An image with no name suffix has an OCR suffix by its type.
            suffix = suffix or attachment_ocr.image_suffix("", row.mime_type or "") or ""
            options["deadline"] = deadline
        elif attachment_ocr.ocr_enabled():
            options["ocr"] = False  # the caller asked for no OCR
        parsed = await parse_bounded(got.content, suffix, **options)
        if isinstance(parsed, str):
            # Not cached: a busy pool and a deadline pass with time.
            _log.info("attachment_text.refused", aid=aid, kind=suffix, size=len(got.content))
            kind = "busy" if isinstance(parsed, ReaderBusy) else "unreadable"
            return AttachmentTextModel(**base, kind=kind, reason=str(parsed))
        answer = _parsed_answer(base, parsed, aid, len(got.content))
        await _remember_text(org_id, row, answer, namespace)
        return _text_part(answer, offset)
    finally:
        if owned and not owned[0].task.done():
            # The fetch runs on, and holds the member's hold until it ends.
            owned[0].detached = True
        else:
            hold.release()


async def _ocr_fetch(
    key: tuple[str, str, str], fetch_alone: Callable[[], Awaitable[Any]], hold: Any,
    aid: str, owned: list[_FetchJob],
) -> Any:
    """The owned fetch of a read with OCR (fix round 4).

    A retry of the same read joins the fetch that runs already, and starts
    none. Else it starts the fetch, puts its job in *owned*, and waits
    :data:`FETCH_TIMEOUT_S`. At that timeout the fetch runs on under the cap,
    or is cancelled above it. A joined fetch took the hold of the request
    that started it, so this request takes its own hold for its parse, or
    answers busy.
    """
    running = _RUNNING_FETCHES.get(key)
    if running is None:
        job = _start_fetch(key, fetch_alone(), hold)
        owned.append(job)
        return await _fetch_for_text(
            asyncio.shield(job.task), aid, FETCH_TIMEOUT_S,
            on_timeout=lambda: _keep_or_cancel(job),
        )
    got = await _fetch_for_text(asyncio.shield(running.task), aid, FETCH_TIMEOUT_S)
    if not got.stopped and not hold.take():
        busy = AttachmentTextModel(**_base(got.row), kind="unreadable", reason=_BUSY_REASON)
        return _OwnedAttachment(row=got.row, stopped=busy)
    return got


async def _fetch_for_text(
    fetch: Awaitable[Any], aid: str, seconds: float | None,
    on_timeout: Callable[[], None] | None = None,
) -> Any:
    """Await the owned *fetch*, within *seconds* when it is given.

    A fault of the database keeps its own answer (review round 1). A fetch
    past *seconds* is 504 (WS-67 OA-3 fix round 2), and any other fault of
    the provider is 502. *on_timeout* runs before the 504 (fix round 4).
    """
    try:
        if seconds is None:
            return await fetch
        return await asyncio.wait_for(fetch, seconds)
    except HTTPException:
        raise
    except TimeoutError:
        if on_timeout is not None:
            on_timeout()
        _log.warning("attachment_text.fetch_slow", aid=aid, seconds=seconds)
        raise HTTPException(
            status_code=504, detail="The mail provider did not give the file in time.",
        ) from None
    except Exception as exc:
        if _is_database_fault(exc):
            raise
        _log.error("attachment_text.fetch_failed", aid=aid, error=str(exc)[:200])
        raise HTTPException(
            status_code=502, detail="The mail provider did not give the file.",
        ) from None


def _parsed_answer(
    base: dict[str, str], parsed: Any, aid: str, size: int,
) -> AttachmentTextModel:
    """The answer to cache for what the reader gave."""
    state = text_state(parsed)
    if state == "unread":
        return AttachmentTextModel(**base, kind="unreadable", reason=_NO_PAGE_REASON)
    if state == "none":
        reason = attachment_ocr.no_words_sentence(parsed) if parsed.ocr else _NO_TEXT_REASON
        return AttachmentTextModel(**base, kind="no_text", reason=reason)
    full = parsed.text
    _log.info("attachment_text.read", aid=aid, kind=parsed.kind,
              size=size, chars=len(full), stopped=parsed.stopped)
    # The text goes in the cache, up to MAX_CACHED_TEXT_CHARS. `truncated`
    # here says that the reader or the cap stopped early, and `_text_part`
    # cuts the reply.
    kept = full[:MAX_CACHED_TEXT_CHARS]
    return AttachmentTextModel(
        **base,
        kind=parsed.kind,
        text=kept,
        truncated=bool(parsed.stopped) or len(full) > len(kept),
        chars=len(kept),
    )
