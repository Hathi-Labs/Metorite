"""Transport · attachments — attachment download and the sandboxed image proxy."""

from __future__ import annotations

import asyncio
import io
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from gateway.routes.email.core import (
    ATTACHMENT_CACHE_TTL_SECS,
    _tenant_session,
    _get_redis,
    _log,
    provider_session,
    router,
)
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


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(
    attachment_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Proxy download an email attachment, streaming from the provider.

    Checks Redis cache first (TTL 1 hour) to avoid redundant provider API
    calls for attachments downloaded multiple times.
    """
    async with _tenant_session() as db:
        try:
            # Look up attachment and verify user owns the parent message
            result = await db.execute(
                text(
                    """SELECT ea.id, ea.filename, ea.mime_type, ea.size_bytes,
                              ea.provider_attachment_id, ea.storage_path,
                              em.provider_message_id, em.account_id
                       FROM email_attachments ea
                       JOIN email_messages em ON ea.message_id = em.id
                       JOIN email_accounts p ON em.account_id = p.id
                       WHERE ea.id = :aid AND p.user_id = :user_id"""
                ),
                {"aid": attachment_id, "user_id": user.email or "anonymous"},
            )
            row = result.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Attachment not found")

            # Sanitise the filename for the Content-Disposition header — strip quotes
            # / CR / LF so an attacker-controlled attachment name can't break out of
            # the quoted value (header-injection / filename-spoofing).
            safe_name = (
                (row.filename or "attachment")
                .replace('"', "'").replace("\n", " ").replace("\r", " ")
            )

            # ── Ownership confirmed — NOW it's safe to serve from the Redis cache.
            #    Reading the cache before this check was an IDOR: any caller who knew
            #    an attachment_id could pull another user's cached bytes. ──
            redis = await _get_redis()
            if redis:
                try:
                    cached = await redis.get(f"email:att:cache:{attachment_id}")
                    if cached:
                        return StreamingResponse(
                            io.BytesIO(cached),
                            media_type=row.mime_type or "application/octet-stream",
                            headers={
                                "Content-Disposition": (
                                    f'attachment; filename="{safe_name}"'
                                ),
                                "Content-Length": str(len(cached)),
                                "X-Cache": "HIT",
                            },
                        )
                except Exception:
                    redis = None  # fall through to provider fetch

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

            # ── Store in Redis cache ──
            if redis and content:
                try:
                    cache_key = f"email:att:cache:{attachment_id}"
                    await redis.setex(
                        cache_key, ATTACHMENT_CACHE_TTL_SECS, content
                    )
                except Exception:
                    pass

            return StreamingResponse(
                io.BytesIO(content),
                media_type=row.mime_type,
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{safe_name}"'
                    ),
                    "Content-Length": str(len(content)),
                    "X-Cache": "MISS",
                },
            )
        except HTTPException:
            raise
        except Exception as exc:
            _log.error("download_attachment.failed", aid=attachment_id, error=str(exc)[:200])
            raise HTTPException(status_code=500, detail="Failed to download attachment")
