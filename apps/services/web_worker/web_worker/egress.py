"""The egress route of the web worker (WEB-6, ``web_research.md`` section 5.1).

No proxy of the fetch guard exists, so the worker holds the check. Each
request of a page passes :func:`route_handler`: the top URL, each frame and
each subresource. The handler never lets Chromium fetch. It gives the request
to ``fetch.pinned_fetch``, and fulfills the request with the answer.

* ``pinned_fetch`` parses each hop strictly, checks its host with
  ``acb_common.public_address`` (the one address rule), and connects to the
  address that passed. A second DNS answer never reaches a socket.
* Playwright does not route the next hop of a redirect. The fetch follows
  each redirect itself, so Chromium never sees a ``3xx``.
* Each answer has a cap of decoded bytes. Each job has a cap of requests,
  of decoded bytes and of fetches at once, and a deadline. When the job
  ends, its scope cancels each fetch that still runs.
* Each refusal and each fault gets an abort (fail closed).

Chromium also gets a proxy address where nothing listens (``render.py``). So
a connection that no route sees, such as a preconnect, goes nowhere.

Fence: ``tests/unit/test_web_worker.py``.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from web_worker.fetch import (
    JobScope,
    Refused,
    TooLarge,
    fulfil_headers,
    host_of,
    pinned_fetch,
)
from web_worker.limits import MAX_DOCUMENT_BYTES, MAX_PAGES_PER_JOB, MAX_SUBRESOURCE_BYTES

log = logging.getLogger("web_worker.egress")

#: The resource types that the worker never fetches. A media stream can be
#: large, and the Markdown of a page does not need it.
SKIPPED_TYPES = frozenset({"media"})

#: The abort reason that Chromium shows for a refused request.
ABORT_CODE = "blockedbyclient"


@dataclass
class EgressLog:
    """What the route saw in one job. One job holds one of these."""

    documents: int = 0
    refused: list[str] = field(default_factory=list)
    #: The URL after the redirects of the top document, for the Markdown links.
    top_url: str | None = None
    #: The reason when the route refused the top document or a hop of it.
    top_refused: str | None = None


def _is_top(request: Any) -> bool:
    """True for the navigation of the main frame. A fault gives False."""
    try:
        return request.frame.parent_frame is None and request.is_navigation_request()
    except Exception:  # a request with no frame is not the top document
        return False


Fetch = Callable[..., Any]


def route_handler(
    record: EgressLog,
    scope: JobScope,
    fetch: Fetch = pinned_fetch,
) -> Callable[[Any, Any], Any]:
    """The handler for ``context.route(ALL, ...)`` of one job.

    ``scope`` holds the deadline, the slots, the checker and the budget of
    the job. A test gives a fake route and a fake request, so the handler
    runs with no Chromium."""

    async def _refuse(route: Any, request: Any, host: str, reason: str) -> None:
        record.refused.append(f"{host} {reason}")
        if getattr(request, "resource_type", "") == "document" and _is_top(request):
            record.top_refused = reason
        log.info("web_worker refused host=%s reason=%s", host, reason)
        await route.abort(ABORT_CODE)

    async def handle(route: Any, request: Any) -> None:
        url = getattr(request, "url", "")
        try:
            is_document = request.resource_type == "document"
            if request.resource_type in SKIPPED_TYPES:
                raise Refused(host_of(url), "type")
            if is_document:
                record.documents += 1
                if record.documents > MAX_PAGES_PER_JOB:
                    raise Refused(host_of(url), "page limit")
            answer = await fetch(
                url,
                method=request.method,
                headers=await request.all_headers(),
                body=request.post_data_buffer,
                cap=MAX_DOCUMENT_BYTES if is_document else MAX_SUBRESOURCE_BYTES,
                scope=scope,
            )
            if is_document and _is_top(request):
                record.top_url = answer.url
            await route.fulfill(status=answer.status, headers=fulfil_headers(answer.headers),
                                body=answer.body)
        except Refused as exc:
            with contextlib.suppress(Exception):  # the route can be gone already
                await _refuse(route, request, exc.host, exc.reason)
        except TooLarge:
            with contextlib.suppress(Exception):
                await _refuse(route, request, host_of(url), "too large")
        except Exception:  # each other fault fails closed
            log.info("web_worker route fault host=%s", host_of(url))
            with contextlib.suppress(Exception):
                await route.abort(ABORT_CODE)

    return handle
