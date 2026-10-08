"""Ride through a short Router restart. Retry a REFUSED connection, and only that.

Owner report, 2026-10-08. ``acb-gateway`` restarted eight times in 36
minutes. Every agent sends its model call to our own Router on the same box
(``/v1/chat/completions`` on localhost). While the gateway restarts, nothing
listens on that port, the connection is refused, and the member's turn ends
in ``APIConnectionError``. A restart takes about ten seconds.

## What is retried

:class:`RideThroughTransport` retries ``httpx.ConnectError`` and nothing else.
That error means the TCP connection never opened, so the request never left
this process. Nothing reached the Router, nothing reached a model, and
nothing can be billed. Retrying it cannot run anything twice.

**Never retried:**

* A failure after the response started. The transport returns when the
  headers arrive, and a streamed body fails later, in the caller's read. No
  code here sees that failure, so a token that streamed is never sent again.
* ``httpx.ConnectTimeout``. The OpenAI SDK retries a timeout itself.
* Any HTTP status. The SDK retries a 429 and a 5xx itself, and a 402 or a
  403 is an answer.

A tool that ran is safe too. A tool runs between two model calls, and only
the NEXT model call can be refused. Retrying that call sends it once. Without
the retry the turn fails, and the member's Retry runs the whole turn and the
tool again.

## How it composes with the retries that exist

* **The OpenAI SDK** retries a connection error twice, after about 0.5 s and
  1 s. That is 1.5 s, far short of a restart. The SDK stamps each try with
  ``x-stainless-retry-count``, and this transport rides through on try ``0``
  ONLY. The SDK's own tries then pass straight through, so the worst case is
  one ride-through plus 1.5 s, never three ride-throughs.
* **The executor's Tier 2 fallback** runs when Tier 1 fails before any
  output. Tier 2 builds a new request, so it gets its own ride-through. The
  worst case for a turn is about two times 16 s, far inside the 120 s idle
  watchdog and the 310 s chat timeout.

## ⚠️ What it does NOT save, measured on the box (2026-10-08)

The agents run INSIDE ``acb-gateway``, and ``LITELLM_BASE_URL`` is
``http://127.0.0.1:8080``, the gateway's own port. A ``systemctl restart``
stops the old process first. uvicorn closes the port and then waits for open
connections, and the member's chat stream is one of them. So a run in the
process that restarts calls a port that cannot open again until that same
run ends. The ride-through then waits 14 s and the turn fails with the
``connection`` code, as before. The restart takes 14 s longer.

It does save a run in ANY other process that calls the gateway's ``/v1``
while the gateway restarts, and a call to a Router that restarts on its own.
A turn in the restarting process needs a listener that does not restart
with the gateway, or a bounded graceful shutdown. Both are deploy choices.

Fence: ``tests/unit/test_router_ride_through.py``.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx
from acb_common import get_logger

__all__ = ["RIDE_THROUGH_DEADLINE_S", "RIDE_THROUGH_DELAYS", "RideThroughTransport"]

_log = get_logger("acb_llm.ride_through")

#: The waits before each retry. Three retries, 14 s of waiting, so four tries
#: span a ~10 s gateway restart with room to spare.
RIDE_THROUGH_DELAYS: tuple[float, ...] = (2.0, 4.0, 8.0)

#: No retry starts after this many seconds from the first try.
RIDE_THROUGH_DEADLINE_S = 15.0

#: The OpenAI SDK's own retry counter, on every request it sends.
_SDK_RETRY_HEADER = "x-stainless-retry-count"


class RideThroughTransport(httpx.AsyncBaseTransport):
    """Wrap a transport, and retry a refused connection with a backoff."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        delays: tuple[float, ...] = RIDE_THROUGH_DELAYS,
        deadline_s: float = RIDE_THROUGH_DEADLINE_S,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._delays = delays
        self._deadline_s = deadline_s
        self._sleep = sleep
        self._clock = clock

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # The SDK's own retries pass straight through (see the module doc).
        ride = request.headers.get(_SDK_RETRY_HEADER, "0").strip() in ("", "0")
        started = self._clock()
        tries = 0
        while True:
            try:
                return await self._inner.handle_async_request(request)
            except httpx.ConnectError as exc:
                if not ride or tries >= len(self._delays):
                    raise
                delay = self._delays[tries]
                if self._clock() - started + delay > self._deadline_s:
                    raise
                tries += 1
                _log.warning(
                    "router_connect_retry",
                    attempt=tries,
                    of=len(self._delays),
                    delay_s=delay,
                    host=request.url.host,
                    port=request.url.port,
                    path=request.url.path,
                    error=type(exc).__name__,
                )
                await self._sleep(delay)

    async def aclose(self) -> None:
        await self._inner.aclose()
