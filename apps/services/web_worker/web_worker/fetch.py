"""The pinned fetch of the web worker (WEB-6, ``web_research.md`` section 5.1).

Chromium never opens a connection for a page. The route (``egress.py``)
gives each request to :func:`pinned_fetch`, and fulfills it with the answer.

* :func:`parse_hop` refuses a URL with a backslash, a control character, a
  space or userinfo, and a scheme that is not ``http`` or ``https``. Two URL
  parsers can disagree on such a URL, so the worker never reads it.
* :func:`port_refusal` keeps each hop on port 80, 443 or the port of the top
  URL, and never on a port of ``UNSAFE_PORTS``.
* :class:`AddressChecker` resolves the host ONCE with
  ``acb_common.public_address.pinned_public_address``, the one address rule.
  It runs in a bounded pool, with a cap per organization and a timeout.
* :class:`PinnedTransport` connects to THAT address. The ``Host`` header and
  the TLS name stay the name of the URL. A second DNS answer (DNS
  rebinding) never reaches a socket.
* The fetch follows each redirect itself, and each hop passes the steps
  again. It reads at most a cap of decoded bytes for each answer and for the
  job, and a bigger answer raises :class:`TooLarge`.
* :class:`JobScope` holds the work of one job. Each fetch and each check
  runs inside the deadline of the job. When the job ends, ``close`` cancels
  each fetch and each check that waits, at once.

The transport pattern is that of ``gateway.outbound_guard._PinnedTransport``.
The address rule is not copied. Fence: ``tests/unit/test_web_worker.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import weakref
import zlib
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx
from acb_common.public_address import PublicAddressRefused, pinned_public_address

from web_worker.limits import (
    CHECK_TIMEOUT_S,
    CHECKS_PER_ORG,
    DEFAULT_PORTS,
    FETCHES_PER_JOB,
    HTTP_TIMEOUT_S,
    JOB_TIMEOUT_S,
    MAX_FETCHES,
    MAX_JOB_BYTES,
    MAX_REDIRECTS,
    MAX_REQUESTS_PER_JOB,
    MAX_SESSIONS,
    UNSAFE_PORTS,
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

#: The ONE pool of address checks. Each organization holds at most
#: CHECKS_PER_ORG of its threads, also with threads of ended jobs that still
#: hang. So the jobs of MAX_SESSIONS organizations always find a thread.
_POOL = ThreadPoolExecutor(max_workers=MAX_SESSIONS * CHECKS_PER_ORG,
                           thread_name_prefix="web-worker-check")

T = TypeVar("T")


class Refused(Exception):
    """The fetch refuses a URL. ``host`` is the host, never the path."""

    def __init__(self, host: str, reason: str) -> None:
        self.host = host[:100]
        self.reason = reason
        super().__init__(f"{self.host} {reason}")


class TooLarge(Exception):
    """An answer, or the bytes of the job, are bigger than the cap."""


# ── Slots of the event loop ─────────────────────────────────────────────────

_LOOP_SLOTS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]]
_LOOP_SLOTS = weakref.WeakKeyDictionary()


def _slots(name: str, size: int) -> asyncio.Semaphore:
    """The semaphore ``name`` of the running loop. A semaphore binds to one
    loop, so each loop gets its own set."""
    per_loop = _LOOP_SLOTS.setdefault(asyncio.get_running_loop(), {})
    if name not in per_loop:
        per_loop[name] = asyncio.Semaphore(size)
    return per_loop[name]


# ── The parse and the port ──────────────────────────────────────────────────


def host_of(url: str) -> str:
    """The host of ``url`` for a log line or a refusal, never the path."""
    try:
        return (urlsplit(url).hostname or "")[:100]
    except Exception:  # a URL that does not parse has no host to name
        return ""


def parse_hop(url: str) -> httpx.URL:
    """``url`` as an ``httpx.URL``, or :class:`Refused`.

    Python's ``urlsplit`` and httpx must agree on the host, or the URL is
    refused. An ASCII host must equal httpx's ASCII host (``raw_host``). A
    host with other letters (IDN) must equal httpx's decoded host. A URL with
    userinfo is refused, so ``a@b`` never names two hosts."""
    host = host_of(url)
    if not isinstance(url, str) or _BAD_CHAR.search(url):
        raise Refused(host, "bad url")
    try:
        parts = urlsplit(url)
        parsed = httpx.URL(url)
        raw = parsed.raw_host.decode("ascii").lower()
    except Exception:  # httpx raises InvalidURL, TypeError or a decode error
        raise Refused(host, "bad url") from None
    if parts.scheme.lower() not in ALLOWED_SCHEMES or parsed.scheme not in ALLOWED_SCHEMES:
        raise Refused(host, "scheme")
    if "@" in parts.netloc or parsed.userinfo:
        raise Refused(host, "userinfo")
    split = (parts.hostname or "").lower()
    same = split == raw if split.isascii() else split == parsed.host.lower()
    if not raw or not same:
        raise Refused(host, "bad url")
    return parsed


def port_of(url: httpx.URL) -> int:
    return url.port or (443 if url.scheme == "https" else 80)


def port_refusal(url: httpx.URL, top_port: int | None) -> str | None:
    """A reason to refuse the port of ``url``, or None.

    A hop may use port 80, 443 or ``top_port``, the port of the top URL of
    the job. No hop uses a port of ``UNSAFE_PORTS``."""
    port = port_of(url)
    if port in UNSAFE_PORTS:
        return "port"
    if port not in DEFAULT_PORTS and port != top_port:
        return "port"
    return None


# ── The address check ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Target:
    """One hop: the URL, its ASCII host and the ONE address that passed."""

    url: httpx.URL
    host: str
    ip: str


Resolve = Callable[[str, int], str]


class AddressChecker:
    """The address check of ONE job.

    The check runs in :data:`_POOL`. Each organization has ``per_org`` slots
    for all its jobs, and a slot comes back only when its thread ends. So a
    resolver that hangs holds at most ``per_org`` threads, also after its job
    ended, and the other organizations still get theirs. The wait for a slot
    and the resolve together have ``timeout``.

    The checker keeps the answer for each host and port for the rest of the
    job: the address that passed, or the refusal. Each later request to that
    host connects to the same checked address.

    :meth:`close` ends the checker. A check that waits stops, and a check
    that waits in the pool queue never runs."""

    def __init__(
        self,
        resolve: Resolve = pinned_public_address,
        *,
        org: str = "",
        pool: ThreadPoolExecutor = _POOL,
        per_org: int = CHECKS_PER_ORG,
        timeout: float = CHECK_TIMEOUT_S,
    ) -> None:
        self._resolve = resolve
        self._org = org
        self._pool = pool
        self._per_org = per_org
        self._timeout = timeout
        self._known: dict[tuple[str, int], asyncio.Future[str]] = {}
        self._queued: set[Future[str]] = set()
        self.closed = False

    def _run(self, host: str, port: int) -> str:
        """The work of one pool thread. A check of a closed job does nothing."""
        if self.closed:
            raise Refused(host, "job ended")
        return self._resolve(host, port)

    async def _check(self, host: str, port: int) -> str:
        loop = asyncio.get_running_loop()
        slots = _slots(f"org-check:{self._org}:{self._per_org}", self._per_org)
        try:
            async with asyncio.timeout(self._timeout):
                await slots.acquire()
                done = self._pool.submit(self._run, host, port)
                self._queued.add(done)

                def _back(f: Future[str]) -> None:
                    self._queued.discard(f)
                    with contextlib.suppress(RuntimeError):  # the loop is closed
                        loop.call_soon_threadsafe(slots.release)

                done.add_done_callback(_back)
                return await asyncio.wrap_future(done)
        except TimeoutError:
            raise Refused(host, "check timeout") from None
        except Refused:
            raise
        except PublicAddressRefused as exc:
            raise Refused(host, exc.reason) from None
        except Exception:  # each other fault of the check fails closed
            raise Refused(host, "check failed") from None

    async def pin(self, url: httpx.URL) -> Target:
        host = url.raw_host.decode("ascii")
        key = (host, port_of(url))
        if self.closed:
            raise Refused(host, "job ended")
        known = self._known.get(key)
        if known is None:
            known = asyncio.ensure_future(self._check(*key))
            self._known[key] = known
        try:
            ip = await asyncio.shield(known)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if known.cancelled() and task is not None and not task.cancelling():
                raise Refused(host, "job ended") from None
            raise
        except Refused:
            raise
        except Exception:  # each other fault of the check fails closed
            raise Refused(host, "check failed") from None
        return Target(url=url, host=host, ip=ip)

    async def refusal(self, url: str, top_port: int | None = None) -> str | None:
        """A reason to refuse ``url``, or None. The fetch checks again."""
        try:
            parsed = parse_hop(url)
            reason = port_refusal(parsed, port_of(parsed) if top_port is None else top_port)
            if reason is not None:
                return reason
            await self.pin(parsed)
        except Refused as exc:
            return exc.reason
        return None

    def close(self) -> None:
        self.closed = True
        for queued in list(self._queued):
            queued.cancel()  # a thread that runs already ends on its own
        for known in self._known.values():
            known.cancel()


# ── The transport ───────────────────────────────────────────────────────────


def _inner_transport() -> httpx.AsyncBaseTransport:
    """The transport that opens the socket. A test replaces it."""
    return httpx.AsyncHTTPTransport(retries=0)


class PinnedTransport(httpx.AsyncBaseTransport):
    """Sends the request of ONE hop to the address of its target.

    The ``Host`` header keeps the name of the URL, and ``sni_hostname`` carries
    its ASCII form for TLS, so the certificate is checked against the name."""

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


# ── The budget and the scope of a job ───────────────────────────────────────


class Budget:
    """The requests and the decoded bytes of ONE job."""

    def __init__(self, cap: int = MAX_REQUESTS_PER_JOB, byte_cap: int = MAX_JOB_BYTES) -> None:
        self.cap = cap
        self.used = 0
        self.byte_cap = byte_cap
        self.bytes = 0
        #: The most bytes that the job held at once, for the fence.
        self.peak = 0

    def take(self, host: str) -> None:
        self.used += 1
        if self.used > self.cap:
            raise Refused(host, "request limit")

    def left(self) -> int:
        """The decoded bytes that the job can still read."""
        return max(self.byte_cap - self.bytes, 0)

    def take_bytes(self, count: int) -> None:
        """Count ``count`` decoded bytes. Each read counts while it reads, so
        the reads of a job at once never pass the cap together."""
        self.bytes += count
        self.peak = max(self.peak, self.bytes)
        if self.bytes > self.byte_cap:
            raise TooLarge("job bytes")

    def give_back(self, count: int) -> None:
        """A read that failed gives its bytes back. Its buffer is gone."""
        self.bytes -= count


class JobScope:
    """The fetches and the checks of ONE job, and their end.

    :meth:`run` runs one unit of work in a task of its own, under the
    deadline of the job. :meth:`close` cancels each such task and closes the
    checker. A unit that starts after ``close`` raises :class:`Refused`."""

    def __init__(
        self,
        *,
        org: str = "",
        top_port: int | None = None,
        deadline_s: float = JOB_TIMEOUT_S,
        checker: AddressChecker | None = None,
        budget: Budget | None = None,
    ) -> None:
        self.checker = checker or AddressChecker(org=org)
        self.budget = budget or Budget()
        self.top_port = top_port
        self._deadline_s = deadline_s
        self._end: float | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._fetch_slots: asyncio.Semaphore | None = None
        self.closed = False

    def _deadline(self) -> float:
        if self._end is None:
            self._end = asyncio.get_running_loop().time() + self._deadline_s
        return self._end

    async def run(self, work: Callable[[], Awaitable[T]], host: str = "") -> T:
        """``work()`` in a task of its own, inside the deadline of the job,
        with a fetch slot of the job and one of the container."""
        if self.closed:
            raise Refused(host, "job ended")
        end = self._deadline()
        if self._fetch_slots is None:
            self._fetch_slots = asyncio.Semaphore(FETCHES_PER_JOB)
        job_slots = self._fetch_slots

        async def _unit() -> T:
            async with asyncio.timeout_at(end):
                async with job_slots, _slots("fetch", MAX_FETCHES):
                    return await work()

        task = asyncio.ensure_future(_unit())
        self._tasks.add(task)
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            self._tasks.discard(task)
        try:
            if task.cancelled():
                raise Refused(host, "job ended")
            exc = task.exception()
            if isinstance(exc, TimeoutError):
                raise Refused(host, "deadline")
            if exc is not None:
                raise exc.with_traceback(None)
            return task.result()
        finally:
            # The exception, its traceback and this frame form a cycle
            # through ``task``. Break it, so a failed read frees at once.
            del task

    def busy(self) -> int:
        """The units of work that run now."""
        return sum(1 for t in self._tasks if not t.done())

    def close(self) -> None:
        self.closed = True
        for task in list(self._tasks):
            task.cancel()
        self.checker.close()


# ── The read ────────────────────────────────────────────────────────────────


def _deflate(first: bytes) -> Any:
    """A decoder for ``deflate``: the zlib form when the first two bytes are a
    zlib header, else the raw form. Servers send both."""
    if len(first) >= 2 and first[0] & 0x0F == 8 and ((first[0] << 8) | first[1]) % 31 == 0:
        return zlib.decompressobj()
    return zlib.decompressobj(-zlib.MAX_WBITS)


def _decoder(resp: httpx.Response, host: str) -> tuple[str, Any]:
    """The content encoding of ``resp`` and its first decoder. ``deflate``
    gets its decoder at the first chunk (:func:`_deflate`)."""
    encoding = resp.headers.get("content-encoding", "").strip().lower()
    if encoding not in ("", "identity", "gzip", "x-gzip", "deflate"):
        raise Refused(host, "encoding")
    if encoding in ("gzip", "x-gzip"):
        return encoding, zlib.decompressobj(16 + zlib.MAX_WBITS)
    return encoding, None


async def read_capped(resp: httpx.Response, cap: int, host: str = "",
                      budget: Budget | None = None) -> bytes:
    """The decoded body of ``resp``, or :class:`TooLarge` past ``cap`` bytes,
    or past the bytes left in ``budget``.

    It reads the raw bytes and decodes gzip and deflate here with a bound, so
    a small compressed answer (a bomb) cannot grow past the cap in memory.
    The wire bytes have the same cap. Another encoding, and a body that does
    not decode, raise ``Refused``."""
    encoding, inflate = _decoder(resp, host)
    buf = bytearray()
    wire = 0
    counted = 0

    def room() -> int:
        """The most bytes that one decode step may give: one past the cap of
        the answer or of the job, so a step past either is seen."""
        left = cap - len(buf)
        if budget is not None:
            left = min(left, budget.left())
        return left + 1

    def count(grown: int) -> None:
        nonlocal counted
        if budget is not None:
            counted += grown
            budget.take_bytes(grown)

    try:
        async for chunk in resp.aiter_raw():
            wire += len(chunk)
            if wire > cap:
                raise TooLarge("wire")
            if encoding == "deflate" and inflate is None:
                inflate = _deflate(chunk)
            before = len(buf)
            buf += chunk if inflate is None else inflate.decompress(chunk, room())
            count(len(buf) - before)
            if len(buf) > cap or (inflate is not None and inflate.unconsumed_tail):
                raise TooLarge("decoded")
        if inflate is not None:
            before = len(buf)
            buf += inflate.decompress(b"", room())
            count(len(buf) - before)
            if len(buf) > cap or inflate.unconsumed_tail:
                raise TooLarge("decoded")
    except zlib.error:
        buf.clear()
        if budget is not None:
            budget.give_back(counted)
        raise Refused(host, "encoding") from None
    except BaseException:
        # The traceback keeps this frame, so free the buffer now. Else a cycle
        # holds each failed buffer until the garbage collector runs.
        buf.clear()
        if budget is not None:
            budget.give_back(counted)
        raise
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
    scope: JobScope,
    transport: Transport = PinnedTransport,
) -> Answer:
    """Fetch ``url`` with each hop checked and pinned, inside the scope of
    the job. It raises :class:`Refused` or :class:`TooLarge`. Chromium never
    sees a ``3xx``."""
    return await scope.run(
        lambda: _walk(url, method=method, headers=headers, body=body, cap=cap,
                      scope=scope, transport=transport),
        host_of(url),
    )


async def _walk(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    cap: int,
    scope: JobScope,
    transport: Transport,
) -> Answer:
    sent = {k: v for k, v in headers.items() if k.lower() not in _REQUEST_SKIP}
    sent["accept-encoding"] = ACCEPT_ENCODING
    current = url
    first_origin: tuple[str, str, int] | None = None
    for _hop in range(MAX_REDIRECTS + 1):
        scope.budget.take(host_of(current))
        parsed = parse_hop(current)
        reason = port_refusal(parsed, scope.top_port)
        if reason is not None:
            raise Refused(host_of(current), reason)
        target = await scope.checker.pin(parsed)
        origin = (target.url.scheme, target.host, port_of(target.url))
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
                data = await read_capped(resp, cap, target.host, scope.budget)
                kept = [(k, v) for k, v in resp.headers.multi_items()
                        if k.lower() not in _ANSWER_SKIP]
                return Answer(resp.status_code, kept, data, str(target.url))
            finally:
                await resp.aclose()
    raise Refused(host_of(current), "redirects")
