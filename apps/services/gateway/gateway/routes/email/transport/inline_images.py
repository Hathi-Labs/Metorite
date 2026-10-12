"""Transport · inline images — the ``cid:`` pictures of a mail body.

WS-17, the inline images fix (``email_app_master_plan.md`` §16.6). A mail
names a picture of its own MIME tree with ``<img src="cid:<Content-ID>">``.
The browser cannot fetch a ``cid:`` URL, and the reading pane's frame allowed
``data:`` pictures only. So each such picture showed as a broken image with
its alt text. The ingest drops the inline parts from the file list on
purpose (``outlook._outlook_attachments``), so no row of
``email_attachments`` holds them.

The pane now writes ``cid:X`` as ``/api/email/messages/{id}/inline?cid=X``,
and this route answers it:

1. The owner read runs first, with the owner predicate of the HTML route. A
   message of another member is 404, before any cache read.
2. Tenant Redis, inside ``organization_scope``, keyed by the id of the ROW and
   a digest of the Content-ID. A list of the digests of the message says which
   Content-IDs exist, so a name that the message does not hold is 404 with no
   provider call.
3. On a miss, ONE provider read for the whole message, with no session open
   (:meth:`BaseEmailProvider.get_inline_images`). The requests of one message
   that arrive together share it (:class:`_Flight`). A body can name a
   hundred pictures, and one provider read for each would be a hundred.
4. Only a picture goes out. The type comes from the first bytes, never from
   the sender's header, and SVG is never served.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from acb_auth import UserContext, get_current_user
from acb_common.tenant_redis import get_tenant_redis, key, organization_scope
from email_ingestion.providers.base import normalize_content_id
from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from gateway.routes.email.core import (
    ATTACHMENT_CACHE_TTL_SECS,
    _decrypt_credentials,
    _instantiate_provider,
    _log,
    _persist_rotated_creds,
    _tenant_session,
    router,
)
from gateway.routes.email.transport.attachments import _canonical_uuid
from gateway.routes.email.transport.messages import (
    _is_provider_429,
    _provider_retry_after,
)
from sqlalchemy import text

#: The parts that one provider read takes at most. Production on 2026-10-13
#: had a median of 10 ``cid:`` references in a body, and 165 at most. Many of
#: them name one signature logo again.
MAX_INLINE_IMAGES = 100
#: The bytes of one inline picture at most.
MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024
#: The bytes of all the pictures of one message at most.
MAX_INLINE_TOTAL_BYTES = 20 * 1024 * 1024
#: The longest ``cid`` that the route reads.
MAX_CID_CHARS = 512
#: How long a cached picture lives: the TTL of the file cache (1 hour).
INLINE_CACHE_TTL_SECS = ATTACHMENT_CACHE_TTL_SECS
#: The namespaces in tenant Redis. Each key holds the id of the ROW.
INLINE_NAMESPACE = "email-inline"
INLINE_IDS_NAMESPACE = "email-inline-ids"

#: The owner read. The same predicate as ``GET /email/messages/{id}/html``.
_OWNED_MESSAGE_SQL = """SELECT em.id, em.provider_message_id, em.account_id,
                               ea.provider, ea.credentials_encrypted
                        FROM email_messages em
                        JOIN email_accounts ea ON em.account_id = ea.id
                        WHERE em.id = :mid AND ea.user_id = :user_id"""

#: The headers of each picture. ``nosniff`` keeps the browser on the type the
#: route chose, and the CSP and the sandbox stop a direct open of the URL from
#: running anything.
_IMAGE_HEADERS = {
    "Cache-Control": "private, max-age=3600",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


def sniff_image_type(content: bytes) -> str | None:
    """The type of a raster picture from its first bytes, else ``None``.

    The sender writes the declared type, so the route never trusts it. SVG
    is text and can carry a script, so it is never a picture here.
    """
    head = content[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    if head[:2] == b"BM" and len(content) > 26:
        return "image/bmp"
    if head[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    return None


def _digest(content_id: str) -> str:
    """A fixed-length key part for a Content-ID of any length."""
    return hashlib.sha256(content_id.encode("utf-8")).hexdigest()[:32]


# ── One provider read for each message, shared by the requests of a burst ──


@dataclass
class _Flight:
    """The provider read of one message, while requests for it are open.

    The first request takes the lock and reads. Each request that waits
    finds ``pictures`` set and makes no read of its own. The entry goes when
    the last request leaves, so the memory holds a message only while the
    pane loads it. ⚠️ In memory, and that is enough: the gateway runs as ONE
    process (``attachments._READING`` relies on the same fact).
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    holders: int = 0
    pictures: dict[str, tuple[str, bytes]] | None = None
    #: The answer of a read that failed. Each request of the burst gives it
    #: back, so a provider that fails is asked once, not once for each picture.
    failed: HTTPException | None = None


_FLIGHTS: dict[tuple[str, str], _Flight] = {}


@asynccontextmanager
async def _flight(org_id: str | None, row_id: str) -> AsyncIterator[_Flight]:
    name = (str(org_id or ""), row_id)
    flight = _FLIGHTS.get(name)
    if flight is None:
        flight = _FLIGHTS[name] = _Flight()
    flight.holders += 1
    try:
        async with flight.lock:
            yield flight
    finally:
        flight.holders -= 1
        if flight.holders == 0 and _FLIGHTS.get(name) is flight:
            del _FLIGHTS[name]


# ── The cache ───────────────────────────────────────────────────────────────


async def _cached_ids(org_id: str | None, row_id: str) -> set[str] | None:
    """The digests of the pictures of a message, else ``None`` for a miss.

    Only with an organization in the session, and only after the owner
    check. A Redis failure or a bad entry is a miss.
    """
    if not org_id:
        return None
    try:
        with organization_scope(org_id):
            raw = await get_tenant_redis(binary=True).get(
                key(INLINE_IDS_NAMESPACE, row_id))
        if not raw:
            return None
        ids = json.loads(raw)
        return {str(i) for i in ids} if isinstance(ids, list) else None
    except Exception:  # the cache is best effort
        return None


async def _cached_picture(
    org_id: str | None, row_id: str, digest: str,
) -> tuple[str, bytes] | None:
    """``(type, bytes)`` of one cached picture, else ``None``."""
    if not org_id:
        return None
    try:
        with organization_scope(org_id):
            raw = await get_tenant_redis(binary=True).get(
                key(INLINE_NAMESPACE, row_id, digest))
        if not raw:
            return None
        kind, _sep, content = bytes(raw).partition(b"\n")
        mime = kind.decode("ascii", "strict")
        return (mime, content) if content and sniff_image_type(content) == mime else None
    except Exception:  # the cache is best effort
        return None


async def _remember(
    org_id: str | None, row_id: str, pictures: dict[str, tuple[str, bytes]],
) -> None:
    """Cache each picture, then the list of the digests. Best effort.

    The list goes last, so a reader that finds it finds each picture too,
    unless Redis evicted one. A picture that is missing then reads again.
    """
    if not org_id:
        return
    try:
        with organization_scope(org_id):
            redis = get_tenant_redis(binary=True)
            for digest, (mime, content) in pictures.items():
                await redis.setex(
                    key(INLINE_NAMESPACE, row_id, digest), INLINE_CACHE_TTL_SECS,
                    mime.encode("ascii") + b"\n" + content,
                )
            await redis.setex(
                key(INLINE_IDS_NAMESPACE, row_id), INLINE_CACHE_TTL_SECS,
                json.dumps(sorted(pictures)).encode("ascii"),
            )
    except Exception:  # the cache is best effort
        pass


# ── The provider read ───────────────────────────────────────────────────────


async def _read_pictures(row: Any, row_id: str) -> dict[str, tuple[str, bytes]]:
    """Every inline picture of the message, by the digest of its Content-ID.

    No session is open across the provider call. A session opens after it
    only when the provider rotated its tokens, as in the HTML route.
    """
    creds, store = _decrypt_credentials(row.credentials_encrypted)
    provider = _instantiate_provider(row.provider, creds)
    account_id = str(row.account_id)
    try:
        if not await provider.authenticate():
            raise HTTPException(status_code=401, detail="Email account authentication failed")
        parts = await provider.get_inline_images(
            row.provider_message_id,
            max_count=MAX_INLINE_IMAGES, max_bytes=MAX_INLINE_IMAGE_BYTES,
        )
    except HTTPException:
        raise
    except Exception as exc:
        if _is_provider_429(exc):
            wait = _provider_retry_after(exc)
            _log.warning("email.inline.provider_429", account_id=account_id, retry_after=wait)
            raise HTTPException(
                status_code=503,
                detail="The mail provider asked for a pause. Try again later.",
                headers={"Retry-After": str(wait)},
            ) from None
        _log.warning("email.inline.fetch_failed", message_id=row_id,
                     error=type(exc).__name__)
        raise HTTPException(
            status_code=502, detail="The mail provider did not give the picture.",
        ) from None
    finally:
        if provider.credentials_dirty():
            try:
                async with _tenant_session() as db:
                    await _persist_rotated_creds(db, store, account_id, provider)
            except Exception as exc:  # never fail the read on it
                _log.warning("email.inline.creds_persist_failed",
                             message_id=row_id, error=type(exc).__name__)

    pictures: dict[str, tuple[str, bytes]] = {}
    total = 0
    for part in parts:
        mime = sniff_image_type(part.content)
        cid = normalize_content_id(part.content_id)
        if mime is None or not cid or len(part.content) > MAX_INLINE_IMAGE_BYTES:
            continue
        if total + len(part.content) > MAX_INLINE_TOTAL_BYTES:
            break
        total += len(part.content)
        pictures.setdefault(_digest(cid), (mime, part.content))
    return pictures


def _picture_response(mime: str, content: bytes) -> Response:
    return Response(content=content, media_type=mime, headers=dict(_IMAGE_HEADERS))


@router.get("/messages/{message_id}/inline")
async def get_inline_image(
    message_id: str,
    cid: str = Query(..., min_length=1, max_length=MAX_CID_CHARS),
    user: UserContext = Depends(get_current_user),
) -> Response:
    """One inline picture of a message of the caller's own mail.

    The reading pane asks for it for each ``<img src="cid:...">`` of a body
    (WS-17, ``email_app_master_plan.md`` §16.6). The organization comes from
    the session and the member from ``user.email``, never from the request.
    A name that the message does not hold, or a part that is not a raster
    picture, is 404.
    """
    mid = _canonical_uuid(message_id)
    want = normalize_content_id(cid)
    if mid is None or not want:
        raise HTTPException(status_code=404, detail="Picture not found")

    # The owner read. The session opens OUTSIDE any try, so `TenantUnbound`
    # and a refused connect reach the handlers of main.py.
    async with _tenant_session() as db:
        row = (await db.execute(
            text(_OWNED_MESSAGE_SQL),
            {"mid": mid, "user_id": user.email or "anonymous"},
        )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Picture not found")
    row_id = str(row.id)
    digest = _digest(want)

    # Ownership confirmed. Only NOW is the cache safe to read.
    org_id = user.organization_id
    known = await _cached_ids(org_id, row_id)
    if known is not None:
        if digest not in known:
            raise HTTPException(status_code=404, detail="Picture not found")
        hit = await _cached_picture(org_id, row_id, digest)
        if hit is not None:
            return _picture_response(*hit)

    async with _flight(org_id, row_id) as flight:
        if flight.failed is not None:
            raise flight.failed
        if flight.pictures is None:
            try:
                flight.pictures = await _read_pictures(row, row_id)
            except HTTPException as exc:
                flight.failed = exc
                raise
            await _remember(org_id, row_id, flight.pictures)
        picture = flight.pictures.get(digest)
    if picture is None:
        raise HTTPException(status_code=404, detail="Picture not found")
    return _picture_response(*picture)
