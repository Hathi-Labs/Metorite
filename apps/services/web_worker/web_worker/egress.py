"""The egress check of the web worker (WEB-6, ``web_research.md`` section 5.1).

No proxy of the fetch guard exists, so the worker holds the check. Each
request of a page passes :func:`route_handler`: the top URL, each redirect
and each subresource. The check is ``acb_common.public_address``, the one
address rule (section 3.1). This module holds no second address rule.

* A scheme that is not ``http`` or ``https`` gets an abort.
* A host that does not resolve, or that has one address that is not public,
  gets an abort. That covers ``127.0.0.1``, ``10.0.0.0/8``,
  ``169.254.169.254`` and ``::1``.
* Each fault in the check gets an abort (fail closed).
* Playwright does not route the next hop of a redirect. So the handler
  follows each redirect itself, up to ``MAX_REDIRECTS``, and checks each hop
  before it fetches it. The browser never sees a ``3xx``.

Fence: ``tests/unit/test_web_worker.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from acb_common.public_address import PublicAddressRefused, pinned_public_address

from web_worker.limits import MAX_PAGES_PER_JOB, MAX_REDIRECTS

log = logging.getLogger("web_worker.egress")

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: The resource types that the worker never fetches. A media stream can be
#: large, and the Markdown of a page does not need it.
SKIPPED_TYPES = frozenset({"media"})

#: The abort reason that Chromium shows for a refused request.
ABORT_CODE = "blockedbyclient"


def target_refusal(url: str) -> str | None:
    """A reason to refuse ``url``, or None when the worker may fetch it.

    Blocking: it resolves the host. Call it from a worker thread. Each fault
    gives a reason, so the check fails closed."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            return "scheme"
        host = parts.hostname
        if not host:
            return "no host"
        port = parts.port or (443 if scheme == "https" else 80)
        pinned_public_address(host, port)
    except PublicAddressRefused as exc:
        return exc.reason
    except Exception:  # any fault in the check refuses
        return "check failed"
    return None


def host_of(url: str) -> str:
    """The host of ``url`` for a log line or a refusal, never the path."""
    try:
        return (urlsplit(url).hostname or "")[:100]
    except Exception:
        return ""


@dataclass
class EgressLog:
    """What the route saw in one job. One job holds one of these."""

    documents: int = 0
    refused: list[str] = field(default_factory=list)
    #: The URL after the redirects of the top document, for the Markdown links.
    top_url: str | None = None


Check = Callable[[str], "str | None"]

#: Request headers that a later hop does not copy. The cookie jar of the
#: context sends the cookie of each host itself.
_HOP_SKIP = frozenset({"host", "cookie", "content-length"})


def _is_top(request: Any) -> bool:
    """True for the navigation of the main frame. A fault gives False."""
    try:
        return request.frame.parent_frame is None and request.is_navigation_request()
    except Exception:
        return False


def route_handler(
    record: EgressLog,
    request_context: Any,
    check: Check = target_refusal,
) -> Callable[[Any, Any], Any]:
    """The handler for ``context.route("**/*", ...)`` of one job.

    Playwright does not route the next hop of a redirect, and Chromium follows
    a ``3xx`` with no route. So the browser never sees a ``3xx`` here. The
    handler follows each redirect itself, checks each hop, and fulfills the
    request with the last answer. ``route.fetch`` gets the first hop, and
    ``request_context`` (``context.request``, the jar of the job) gets each
    later hop.

    ``check`` is :func:`target_refusal`. A test gives a fake check, a fake
    route, a fake request and a fake ``request_context``, so the handler runs
    with no Chromium."""

    async def _refuse(route: Any, url: str, reason: str) -> None:
        record.refused.append(f"{host_of(url)} {reason}")
        log.info("web_worker refused host=%s reason=%s", host_of(url), reason)
        await route.abort(ABORT_CODE)

    async def _refusal(url: str, is_document: bool) -> str | None:
        reason = await asyncio.to_thread(check, url)
        if reason is None and is_document:
            record.documents += 1
            if record.documents > MAX_PAGES_PER_JOB:
                reason = "page limit"
        return reason

    async def handle(route: Any, request: Any) -> None:
        try:
            url = request.url
            is_document = request.resource_type == "document"
            reason = await _refusal(url, is_document)
            if reason is None and request.resource_type in SKIPPED_TYPES:
                reason = "type"
            if reason is not None:
                await _refuse(route, url, reason)
                return
            response = await route.fetch(max_redirects=0)
            method = request.method
            headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_SKIP}
            hops = 0
            while 300 <= response.status < 400 and response.headers.get("location"):
                hops += 1
                url = urljoin(url, response.headers["location"])
                reason = "redirects" if hops > MAX_REDIRECTS else await _refusal(url, is_document)
                if reason is not None:
                    await _refuse(route, url, reason)
                    return
                if response.status == 303 or (response.status in (301, 302) and method == "POST"):
                    method = "GET"
                response = await request_context.fetch(
                    url, method=method, headers=headers, max_redirects=0,
                    data=request.post_data_buffer if method not in ("GET", "HEAD") else None,
                )
            if is_document and _is_top(request):
                record.top_url = url
            await route.fulfill(response=response)
        except Exception:  # each fault fails closed
            log.info("web_worker route fault host=%s", host_of(getattr(request, "url", "")))
            with contextlib.suppress(Exception):  # the route can be gone already
                await route.abort(ABORT_CODE)

    return handle
