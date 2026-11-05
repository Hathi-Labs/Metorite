"""The pinned fetch of the web worker (WEB-6, ``web_research.md`` section 5.1).

Chromium never opens a connection for a page. The route (``egress.py``)
gives each request to :func:`pinned_fetch`, and fulfills it with the answer.

* :func:`parse_hop` refuses a URL with a backslash, a control character, a
  space or userinfo, and a scheme that is not ``http`` or ``https``. Two URL
  parsers can disagree on such a URL, so the worker never reads it.
* :class:`AddressChecker` resolves the host ONCE with
  ``acb_common.public_address.pinned_public_address``, the one address rule.
  It runs in a bounded pool, with a cap per job and a timeout per check.
* :class:`PinnedTransport` connects to THAT address. The ``Host`` header and
  the TLS name stay the name of the URL. A second DNS answer (DNS
  rebinding) never reaches a socket.
* The fetch follows each redirect itself, and each hop passes the three
  steps again. It reads at most a cap of decoded bytes, and a bigger answer
  raises :class:`TooLarge`.

The transport pattern is that of ``gateway.outbound_guard._PinnedTransport``.
The address rule is not copied. Fence: ``tests/unit/test_web_worker.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from acb_common.public_address import PublicAddressRefused, pinned_public_address

from web_worker.limits import (
    CHECK_TIMEOUT_S,
    CHECKS_PER_JOB,
    HTTP_TIMEOUT_S,
    MAX_REDIRECTS,
    MAX_REQUESTS_PER_JOB,
    MAX_SESSIONS,
)

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: A backslash, a control character or a space. Chromium gives a clean URL,
#: so only a page-made ``Location`` can hold one.
_BAD_CHAR = re.compile(r"[\x00-\x20\x7f\\]")

_REDIRECTS = frozenset({301, 302, 303, 307, 308})

#: Request headers that the fetch never copies. httpx writes ``Host`` and
#: ``Content-Length``, and the fetch writes ``Accept-Encoding``.
_REQUEST_SKIP = frozenset({"host", "content-length", "connection", "accept-encoding",
                           "transfer-encoding", "keep-alive", "upgrade", "te", "trailer"})

#: Request headers that a hop to another origin does not get (the rule of
#: the Fetch spec for a redirect).
_CROSS_HOST_SKIP = frozenset({"cookie", "authorization", "proxy-authorization"})

#: Answer headers that the route does not give to Chromium. The body that it
#: gives is decoded, and its length is new.
_ANSWER_SKIP = frozenset({"content-encoding", "content-length", "transfer-encoding",
                          "connection", "keep-alive"})

#: The encodings that the fetch asks for and can decode with a bound.
ACCEPT_ENCODING = "gzip, deflate"

#: The ONE pool of address checks. Each job holds at most CHECKS_PER_JOB of
#: its threads, so a job always finds a free thread.
_POOL = ThreadPoolExecutor(max_workers=MAX_SESSIONS * CHECKS_PER_JOB,
                           thread_name_prefix="web-worker-check")


class Refused(Exception):
    """The fetch refuses a URL. ``host`` is the host, never the path."""

    def __init__(self, host: str, reason: str) -> None:
        self.host = host[:100]
        self.reason = reason
        super().__init__(f"{self.host} {reason}")


class TooLarge(Exception):
    """An answer is bigger than its cap."""


def host_of(url: str) -> str:
    """The host of ``url`` for a log line or a refusal, never the path."""
    try:
        return (urlsplit(url).hostname or "")[:100]
    except Exception:  # a URL that does not parse has no host to name
        return ""


def parse_hop(url: str) -> httpx.URL:
    """``url`` as an ``httpx.URL``, or :class:`Refused`.

    Python's ``urlsplit`` and httpx must agree on the host, or the URL is
    refused. A URL with userinfo is refused, so ``a@b`` never names two hosts."""
    host = host_of(url)
    if not isinstance(url, str) or _BAD_CHAR.search(url):
        raise Refused(host, "bad url")
    try:
        parts = urlsplit(url)
        parsed = httpx.URL(url)
    except Exception:  # httpx raises InvalidURL or TypeError
        raise Refused(host, "bad url") from None
    if parts.scheme.lower() not in ALLOWED_SCHEMES or parsed.scheme not in ALLOWED_SCHEMES:
        raise Refused(host, "scheme")
    if "@" in parts.netloc or parsed.userinfo:
        raise Refused(host, "userinfo")
    if not parsed.host or parsed.host.lower() != (parts.hostname or "").lower():
        raise Refused(host, "bad url")
    return parsed


@dataclass(frozen=True)
class Target:
    """One hop: the URL, its host and the ONE address that passed the check."""

    url: httpx.URL
    host: str
    ip: str


Resolve = Callable[[str, int], str]


class AddressChecker:
    """The address check of ONE job.

    The check runs in :data:`_POOL`. A job holds at most ``per_job`` checks
    at once, and a slot comes back only when its thread ends. So a resolver
    that hangs holds at most ``per_job`` threads, and the other jobs still
    get theirs. A resolve past ``timeout`` refuses the host. The job timeout
    bounds the wait for a slot.

    The checker keeps the answer for each host and port for the rest of the
    job: the address that passed, or the refusal. Each later request to that
    host connects to the same checked address, and no request resolves the
    host again."""

    def __init__(
        self,
        resolve: Resolve = pinned_public_address,
        *,
        pool: ThreadPoolExecutor = _POOL,
        per_job: int = CHECKS_PER_JOB,
        timeout: float = CHECK_TIMEOUT_S,
    ) -> None:
        self._resolve = resolve
        self._pool = pool
        self._slots = asyncio.Semaphore(per_job)
        self._timeout = timeout
        self._known: dict[tuple[str, int], asyncio.Future[str]] = {}

    async def _check(self, host: str, port: int) -> str:
        """The address of ``host``, or :class:`Refused`. One check for each
        host and port of the job."""
        loop = asyncio.get_running_loop()
        await self._slots.acquire()
        done = self._pool.submit(self._resolve, host, port)

        def _back(_f: object) -> None:
            with contextlib.suppress(RuntimeError):  # the loop is closed
                loop.call_soon_threadsafe(self._slots.release)

        done.add_done_callback(_back)
        try:
            async with asyncio.timeout(self._timeout):
                return await asyncio.wrap_future(done)
        except TimeoutError:
            raise Refused(host, "check timeout") from None
        except PublicAddressRefused as exc:
            raise Refused(host, exc.reason) from None
        except Exception:  # each other fault of the check fails closed
            raise Refused(host, "check failed") from None

    async def pin(self, url: httpx.URL) -> Target:
        host = url.host
        port = url.port or (443 if url.scheme == "https" else 80)
        key = (host, port)
        known = self._known.get(key)
        if known is None:
            known = asyncio.ensure_future(self._check(host, port))
            self._known[key] = known
        try:
            ip = await asyncio.shield(known)
        except Refused:
            raise
        except Exception:  # a cancelled check refuses, and it fails closed
            raise Refused(host, "check failed") from None
        return Target(url=url, host=host, ip=ip)

    async def refusal(self, url: str) -> str | None:
        """A reason to refuse ``url``, or None. The fetch checks again."""
        try:
            await self.pin(parse_hop(url))
        except Refused as exc:
            return exc.reason
        return None


def _inner_transport() -> httpx.AsyncBaseTransport:
    """The transport that opens the socket. A test replaces it."""
    return httpx.AsyncHTTPTransport(retries=0)


class PinnedTransport(httpx.AsyncBaseTransport):
    """Sends the request of ONE hop to the address of its target.

    The ``Host`` header keeps the name of the URL, and ``sni_hostname`` carries
    it for TLS, so the certificate is checked against the name."""

    def __init__(self, target: Target, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._target = target
        self._inner = inner or _inner_transport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url, want = request.url, self._target.url
        if (url.scheme, url.raw_host, url.port) != (want.scheme, want.raw_host, want.port):
            raise Refused(self._target.host, "other origin")
        extensions = dict(request.extensions)
        if url.scheme == "https":
            extensions["sni_hostname"] = self._target.host
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=self._target.ip),
            headers=request.headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await self._inner.handle_async_request(pinned)

    async def aclose(self) -> None:
        await self._inner.aclose()


class Budget:
    """The request count of ONE job: each request and each hop."""

    def __init__(self, cap: int = MAX_REQUESTS_PER_JOB) -> None:
        self.cap = cap
        self.used = 0

    def take(self, host: str) -> None:
        self.used += 1
        if self.used > self.cap:
            raise Refused(host, "request limit")


async def read_capped(resp: httpx.Response, cap: int, host: str = "") -> bytes:
    """The decoded body of ``resp``, or :class:`TooLarge` past ``cap`` bytes.

    It reads the raw bytes and decodes gzip and deflate here with a bound, so
    a small compressed answer (a bomb) cannot grow past the cap in memory.
    The wire bytes have the same cap. Another encoding raises ``Refused``."""
    encoding = resp.headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        inflate = None
    elif encoding in ("gzip", "x-gzip"):
        inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        inflate = zlib.decompressobj()
    else:
        raise Refused(host, "encoding")
    buf = bytearray()
    wire = 0
    async for chunk in resp.aiter_raw():
        wire += len(chunk)
        if wire > cap:
            raise TooLarge("wire")
        buf += chunk if inflate is None else inflate.decompress(chunk, cap + 1 - len(buf))
        if len(buf) > cap or (inflate is not None and inflate.unconsumed_tail):
            raise TooLarge("decoded")
    if inflate is not None:
        buf += inflate.flush()
        if len(buf) > cap:
            raise TooLarge("decoded")
    return bytes(buf)


@dataclass
class Answer:
    status: int
    headers: list[tuple[str, str]]
    body: bytes
    url: str


def fulfil_headers(headers: list[tuple[str, str]]) -> dict[str, str]:
    """One value for each name, for ``route.fulfill``. Playwright splits a
    ``Set-Cookie`` value on a newline, so each cookie stays one cookie."""
    out: dict[str, str] = {}
    for name, value in headers:
        key = name.lower()
        if key in out:
            out[key] += ("\n" if key == "set-cookie" else ", ") + value
        else:
            out[key] = value
    return out


Transport = Callable[[Target], httpx.AsyncBaseTransport]


async def pinned_fetch(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    cap: int,
    checker: AddressChecker,
    budget: Budget,
    transport: Transport = PinnedTransport,
) -> Answer:
    """Fetch ``url`` with each hop checked and pinned. It raises
    :class:`Refused` or :class:`TooLarge`. Chromium never sees a ``3xx``."""
    sent = {k: v for k, v in headers.items() if k.lower() not in _REQUEST_SKIP}
    sent["accept-encoding"] = ACCEPT_ENCODING
    current = url
    first_origin: tuple[str, str, int] | None = None
    for _hop in range(MAX_REDIRECTS + 1):
        budget.take(host_of(current))
        target = await checker.pin(parse_hop(current))
        origin = (target.url.scheme, target.host,
                  target.url.port or (443 if target.url.scheme == "https" else 80))
        if first_origin is None:
            first_origin = origin
        elif origin != first_origin:
            sent = {k: v for k, v in sent.items() if k.lower() not in _CROSS_HOST_SKIP}
        # The transport alone, with no client: httpx's client parses each
        # Location itself, and it keeps cookies and reads proxy settings.
        async with transport(target) as hop:
            request = httpx.Request(
                method, target.url, headers=sent, content=body,
                extensions={"timeout": httpx.Timeout(HTTP_TIMEOUT_S).as_dict()},
            )
            resp = await hop.handle_async_request(request)
            try:
                location = resp.headers.get("location")
                if resp.status_code in _REDIRECTS and location is not None:
                    if _BAD_CHAR.search(location):
                        raise Refused(target.host, "bad redirect")
                    try:
                        current = str(target.url.join(location))
                    except Exception:  # httpx raises InvalidURL or TypeError
                        raise Refused(target.host, "bad redirect") from None
                    if resp.status_code == 303 or (resp.status_code in (301, 302)
                                                   and method == "POST"):
                        method, body = "GET", None
                    continue
                data = await read_capped(resp, cap, target.host)
                kept = [(k, v) for k, v in resp.headers.multi_items()
                        if k.lower() not in _ANSWER_SKIP]
                return Answer(resp.status_code, kept, data, str(target.url))
            finally:
                await resp.aclose()
    raise Refused(host_of(current), "redirects")

