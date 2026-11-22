"""A capped read of a vendor reply — one helper for the native handlers.

Task 2452. ``response.json()`` makes httpx read the whole body into memory
first. An operator sets ``api_base`` on the credential row, so a wrong or
hostile host could answer with a huge body. :func:`post_capped` streams the
reply and stops at :data:`MAX_VENDOR_BODY_BYTES`.

A handler turns :class:`VendorBodyTooLarge` into a ``NativeProviderError`` with
no status. That error is retryable, so the chain moves to the next step.

⚠️ It reads no body at all for a status of 400 or above. The handlers never
use an error body, so none of it enters memory.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

__all__ = ["MAX_VENDOR_BODY_BYTES", "CappedReply", "VendorBodyTooLarge", "post_capped"]

#: The most bytes of a vendor reply that a handler reads. A transcript of a
#: 10 minute clip is far below this.
MAX_VENDOR_BODY_BYTES = 1024 * 1024


class VendorBodyTooLarge(Exception):
    """The vendor reply is above the cap. The message holds no body bytes."""


class CappedReply:
    """The status of a reply, and its body when the status is below 400."""

    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        """Parse the capped body. Raises ``ValueError`` as ``response.json()`` does."""
        return json.loads(self._body)


async def post_capped(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int = MAX_VENDOR_BODY_BYTES,
    **kwargs: Any,
) -> CappedReply:
    """POST with a streamed read, and raise :class:`VendorBodyTooLarge` above the cap.

    ``httpx.HTTPError`` passes through, so the caller keeps its own handling.
    """
    async with client.stream("POST", url, **kwargs) as response:
        status = response.status_code
        if status >= 400:
            return CappedReply(status, b"")
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > max_bytes:
            raise VendorBodyTooLarge("vendor reply above the cap")
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise VendorBodyTooLarge("vendor reply above the cap")
            chunks.append(chunk)
    return CappedReply(status, b"".join(chunks))
