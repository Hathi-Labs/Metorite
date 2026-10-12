"""The gateway's ONE guard for a request to a URL that a member supplied.

WS-17 EM-T13b-2, spec ``project-docs/specs/email_app_master_plan.md``
§10.4.15. Two callers use it: the one-click unsubscribe
(``routes/email/automation/senders.py::_http_unsubscribe``) and the rule action
``CALL_WEBHOOK`` (``routes/email/automation/actions.py``). It is a leaf module
beside ``csv_export.py``, for that file's reason: it belongs to neither caller,
and a security control with two copies misses the next fix.

The rules, in order:

1. **The check.** The URL uses ``http`` or ``https`` and has a host. It has no
   user name, no password and no backslash. The guard resolves the host ONCE.
   It unwraps an IPv4-mapped IPv6 address. It refuses the URL when any address
   is multicast, reserved or not ``is_global``, or is in a range that carries
   an IPv4 address (NAT64, IPv4-compatible, IPv4-translated). It also refuses
   an address that is local to this host (review round 1). So one bad address
   in a list of public ones is enough to refuse.
2. **The pin.** The request goes to the FIRST resolved address, with the
   ``Host`` header of the URL. For ``https`` the ``sni_hostname`` extension
   carries the name, so TLS checks the certificate against the name and not
   the address. A second DNS answer (DNS rebinding) can never reach the socket.
   ``trust_env=False``, so no proxy from the environment sees the request.
3. **No redirect.** ``follow_redirects=False``, and a 3xx raises
   :class:`OutboundRefused`. A redirect target never passed the check.
4. **Caps.** 3 seconds to connect and 10 seconds in total. The total covers
   the lookup and every request of one call (:func:`deadline`). The guard
   streams the answer and keeps at most 64 KiB of it. A lookup that runs past
   the total keeps its worker thread until ``getaddrinfo`` returns, because a
   thread cannot be cancelled. That is a known limit.

A refusal raises :class:`OutboundRefused`, which names the host and a short
reason. It never holds the path or the query of the URL.

WS-8c adds :func:`mcp_client`, the client of an MCP session for a native MAF
agent (``orchestrator/_maf_mcp.py``). It is the third caller, and it keeps
rules 1 to 3. The section above :func:`mcp_client` says what it adds.

WS-54 IN-8a adds :func:`request_https`, the mode of each MCP OAuth fetch
(``gateway/connections/mcp_oauth.py``). It is :func:`request` with the two MCP
rules: ``https`` only, on port 443 only.

``_host_is_public`` and ``_is_safe_external_url`` keep the names that they had
in ``senders.py``, which imports them again.

Fence: ``tests/unit/test_email_webhook_guard.py``.
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any

import httpx

#: Seconds to open the connection.
CONNECT_TIMEOUT_S = 3.0
#: Seconds for the whole request, the read of the answer included.
TOTAL_TIMEOUT_S = 10.0
#: The most of an answer that the guard reads.
MAX_ANSWER_BYTES = 64 * 1024

_SCHEMES = ("http", "https")

#: IPv6 ranges that carry an IPv4 address in their low bits. A host can reach
#: the IPv4 address through each one, so the guard refuses the whole range by
#: name, and does not depend on ``is_reserved`` (review round 1). The mapped
#: range ``::ffff:0:0/96`` is unwrapped instead, and its IPv4 address is
#: checked.
_IPV4_CARRIERS = tuple(ipaddress.IPv6Network(n) for n in (
    "64:ff9b::/96",      # NAT64, RFC 6052
    "64:ff9b:1::/48",    # local NAT64, RFC 8215
    "::/96",             # IPv4-compatible, deprecated
    "::ffff:0:0:0/96",   # IPv4-translated (SIIT), RFC 2765
))

#: The longest host that a refusal names. A host name can be 253 characters,
#: so the guard cuts it and the reason always stays in the record.
_HOST_IN_MESSAGE = 100


class OutboundRefused(Exception):  # the spec names this class
    """The guard refused a URL, or a 3xx answer, or a run past the time cap.

    ``host`` is the host of the URL and ``reason`` is a short phrase. Neither
    holds the path or the query."""

    def __init__(self, host: str, reason: str) -> None:
        self.host = host
        self.reason = reason
        shown = host[:_HOST_IN_MESSAGE]
        super().__init__(" ".join(p for p in (shown, reason) if p))


@dataclass(frozen=True)
class Target:
    """A URL that passed the check, and the address that it is pinned to.

    Only :func:`check_url` makes one."""

    url: httpx.URL
    host: str
    ip: str


@dataclass(frozen=True)
class Answer:
    """The status of an answer, and at most :data:`MAX_ANSWER_BYTES` of it.

    ``headers`` are the headers of the answer (WS-54 IN-8a). The MCP OAuth
    discovery reads ``WWW-Authenticate`` from a 401. The lookup ignores case.
    """

    status_code: int
    body: bytes
    headers: httpx.Headers = field(default_factory=httpx.Headers, compare=False)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


def _resolve(host: str, port: int) -> list[str]:
    """Every address of ``host``, in the order of the resolver.

    The guard calls this ONCE for each check. A test replaces it."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in seen:
            seen.append(addr)
    return seen


def _is_local_address(addr: str) -> bool:
    """True when ``addr`` is an address of this host.

    A socket can bind only to an address of this host. Any other address fails
    with EADDRNOTAVAIL. Any other bind failure counts as local, so the check
    fails closed. A host with no stack for the family (no IPv6, for example)
    cannot open the socket, and it cannot reach the address either. The socket
    opens no connection and sends nothing."""
    ip = ipaddress.ip_address(addr)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
    except OSError as exc:
        # Only a missing stack for the family means "cannot be ours". A full
        # descriptor table, or any other fault, fails closed (re-check P3).
        return exc.errno not in _NO_FAMILY_ERRNOS
    with sock:
        try:
            sock.bind((addr, 0))
        except OSError as exc:
            return exc.errno not in _NOT_LOCAL_ERRNOS
    return True


_NOT_LOCAL_ERRNOS = frozenset(
    e for e in (errno.EADDRNOTAVAIL, getattr(errno, "WSAEADDRNOTAVAIL", None))
    if e is not None)

_NO_FAMILY_ERRNOS = frozenset(
    e for e in (errno.EAFNOSUPPORT, getattr(errno, "WSAEAFNOSUPPORT", None))
    if e is not None)


def _address_refusal(addresses: list[str]) -> str | None:
    """A reason to refuse ``addresses``, or None when each one is public."""
    if not addresses:
        return "unresolvable"
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return "unresolvable"
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        site_local = isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local
        if (ip.is_multicast or ip.is_reserved or not ip.is_global or site_local
                or any(ip in net for net in _IPV4_CARRIERS)):
            return "not public"
    for addr in addresses:
        if _is_local_address(addr):
            return "local"
    return None


def _host_is_public(host: str) -> bool:
    """True only when each address of ``host`` is public (the SSRF check)."""
    try:
        addresses = _resolve(host, 443)
    except Exception:  # a host that does not resolve is unsafe
        return False
    return _address_refusal(addresses) is None


async def check_url(url: str) -> Target:
    """Check ``url`` and pin it to its first address, or raise.

    The host resolves ONCE, here. :func:`send` connects to ``Target.ip`` and
    never resolves again."""
    if not isinstance(url, str):
        raise OutboundRefused("", "bad url")
    if "\\" in url:
        raise OutboundRefused("", "backslash")
    try:
        parsed = httpx.URL(url)
    except Exception:  # httpx raises InvalidURL or TypeError
        raise OutboundRefused("", "bad url") from None
    try:
        host = parsed.raw_host.decode("ascii")
    except UnicodeDecodeError:
        raise OutboundRefused("", "bad url") from None
    if parsed.scheme not in _SCHEMES:
        raise OutboundRefused(host, "scheme")
    if not host:
        raise OutboundRefused("", "no host")
    if parsed.userinfo:
        raise OutboundRefused(host, "user name")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_S):
            addresses = await asyncio.to_thread(_resolve, host, port)
    except TimeoutError:
        raise OutboundRefused(host, "timeout") from None
    except Exception:  # a host that does not resolve is unsafe
        raise OutboundRefused(host, "unresolvable") from None
    reason = _address_refusal(addresses)
    if reason is not None:
        raise OutboundRefused(host, reason)
    return Target(url=parsed, host=host, ip=addresses[0])


def deadline() -> asyncio.Timeout:
    """ONE time budget of :data:`TOTAL_TIMEOUT_S` for a whole call.

    A caller that checks once and sends twice puts all three under one
    ``async with deadline():``. Each step also keeps its own cap."""
    return asyncio.timeout(TOTAL_TIMEOUT_S)


def _host_of(url: str) -> str:
    """The host of ``url`` for a refusal, or an empty string."""
    try:
        return httpx.URL(url).raw_host.decode("ascii")
    except Exception:  # any URL that does not parse names no host
        return ""


async def _is_safe_external_url(url: str) -> bool:
    """True when ``url`` passes :func:`check_url`."""
    try:
        await check_url(url)
    except OutboundRefused:
        return False
    return True


async def _read_capped(resp: httpx.Response) -> bytes:
    """At most :data:`MAX_ANSWER_BYTES` of the answer, as it came on the wire.

    It reads raw bytes, so a compressed answer cannot grow past the cap."""
    buf = bytearray()
    async for chunk in resp.aiter_raw():
        buf += chunk[: MAX_ANSWER_BYTES - len(buf)]
        if len(buf) >= MAX_ANSWER_BYTES:
            break
    return bytes(buf)


async def send(
    target: Target, method: str, *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    json: Any = None,
) -> Answer:
    """Send one request to the pinned address of ``target``.

    A 3xx raises :class:`OutboundRefused`, and so does a run past
    :data:`TOTAL_TIMEOUT_S`. A transport fault raises its ``httpx`` error."""
    pinned = target.url.copy_with(host=target.ip)
    sent_headers = dict(headers or {})
    sent_headers["Host"] = target.url.netloc.decode("ascii")
    extensions = (
        {"sni_hostname": target.host} if target.url.scheme == "https" else {})
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_S):
            async with httpx.AsyncClient(
                follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            ) as client:
                req = client.build_request(
                    method, pinned, headers=sent_headers, content=content,
                    json=json, extensions=extensions)
                resp = await client.send(req, stream=True)
                try:
                    if 300 <= resp.status_code < 400:
                        raise OutboundRefused(
                            target.host, f"redirect {resp.status_code}")
                    body = await _read_capped(resp)
                finally:
                    await resp.aclose()
    except TimeoutError:
        raise OutboundRefused(target.host, "timeout") from None
    return Answer(status_code=resp.status_code, body=body, headers=resp.headers)


async def request(
    method: str, url: str, *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    json: Any = None,
) -> Answer:
    """:func:`check_url`, then :func:`send`, for one request.

    One :func:`deadline` covers the lookup and the request together."""
    try:
        async with deadline():
            target = await check_url(url)
            return await send(
                target, method, headers=headers, content=content, json=json)
    except TimeoutError:
        raise OutboundRefused(_host_of(url), "timeout") from None


# ── The MCP client (WS-8c) ──────────────────────────────────────────────────
#
# Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c. An MCP
# session streams for the whole run, so :func:`request` and :func:`send`,
# which read 64 KiB in 10 seconds, cannot carry it. :func:`mcp_client` gives a
# client for the session instead. It keeps the same rules: the URL passes
# :func:`check_url` once, and each request goes to the address that the check
# resolved. It adds two rules for MCP: ``https`` on port 443 only, and each
# request goes to the origin of the URL only.
#
# ⚠️ The MCP SDK follows a redirect on the same origin by itself, and it does
# not read ``follow_redirects``. So the transport refuses each 3xx answer, and
# the SDK never sees one.
#
# Fence: ``tests/unit/test_mcp_outbound_pin.py``.

#: The one port that an MCP server may use.
MCP_PORT = 443
#: The time caps of an MCP session. The read cap is long, because the server
#: streams its answers.
MCP_TIMEOUT = httpx.Timeout(30.0, connect=CONNECT_TIMEOUT_S, read=300.0)
#: Seconds for the session DELETE that ends an MCP session. The run waits on
#: it in its ``finally``, so the long read cap above must not apply (review
#: round 2 of PR #869).
MCP_DELETE_TIMEOUT_S = 2.0


async def check_mcp_url(url: str) -> Target:
    """:func:`check_url` for an MCP server, which must use ``https`` on 443.

    The scheme and the port are checked first, so a refused URL causes no
    DNS lookup."""
    if not isinstance(url, str):
        raise OutboundRefused("", "bad url")
    try:
        parsed = httpx.URL(url)
    except Exception:  # httpx raises InvalidURL or TypeError
        raise OutboundRefused("", "bad url") from None
    host = _host_of(url)
    if parsed.scheme != "https":
        raise OutboundRefused(host, "scheme")
    if (parsed.port or MCP_PORT) != MCP_PORT:
        raise OutboundRefused(host, "port")
    return await check_url(url)


async def request_https(
    method: str, url: str, *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    json: Any = None,
) -> Answer:
    """:func:`check_mcp_url`, then :func:`send`, for one request (WS-54 IN-8a).

    The mode of each MCP OAuth fetch: discovery, registration, token and
    revoke (``integrations_console.md`` §3.6.5). It keeps every rule of
    :func:`request`, and it adds two: ``https`` only, on port 443 only.
    :func:`request` allows ``http``, and :func:`mcp_client` refuses another
    origin, so neither serves the OAuth flow. One :func:`deadline` covers the
    lookup and the request together.

    Fence: ``tests/unit/test_mcp_oauth_guard.py``.
    """
    try:
        async with deadline():
            target = await check_mcp_url(url)
            return await send(
                target, method, headers=headers, content=content, json=json)
    except TimeoutError:
        raise OutboundRefused(_host_of(url), "timeout") from None


def _mcp_inner_transport() -> httpx.AsyncBaseTransport:
    """The transport that opens the socket. A test replaces it."""
    return httpx.AsyncHTTPTransport(retries=0)


class _PinnedTransport(httpx.AsyncBaseTransport):
    """Sends each request to the address of ONE :class:`Target`.

    A request to another origin is refused, and so is a 3xx answer. The
    ``Host`` header keeps the name of the URL, and ``sni_hostname`` carries
    the name, so TLS checks the certificate against the name."""

    def __init__(self, target: Target) -> None:
        self._target = target
        self._origin = (target.url.scheme, target.host, target.url.port or MCP_PORT)
        self._inner = _mcp_inner_transport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        try:
            host = url.raw_host.decode("ascii")
        except UnicodeDecodeError:
            raise OutboundRefused("", "bad url") from None
        if (url.scheme, host, url.port or MCP_PORT) != self._origin:
            raise OutboundRefused(host, "other origin")
        extensions = {**request.extensions, "sni_hostname": self._target.host}
        if request.method == "DELETE":
            cap = MCP_DELETE_TIMEOUT_S
            extensions["timeout"] = {"connect": cap, "read": cap, "write": cap, "pool": cap}
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=self._target.ip),
            headers=request.headers,
            stream=request.stream,
            extensions=extensions,
        )
        response = await self._inner.handle_async_request(pinned)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise OutboundRefused(self._target.host, f"redirect {response.status_code}")
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


async def mcp_client(url: str) -> httpx.AsyncClient:
    """A client for ONE MCP session to ``url``, or :class:`OutboundRefused`.

    The caller owns the client and closes it when the run ends."""
    target = await check_mcp_url(url)
    return httpx.AsyncClient(
        transport=_PinnedTransport(target),
        follow_redirects=False,
        trust_env=False,
        timeout=MCP_TIMEOUT,
    )


__all__ = [
    "CONNECT_TIMEOUT_S",
    "MAX_ANSWER_BYTES",
    "MCP_DELETE_TIMEOUT_S",
    "MCP_PORT",
    "MCP_TIMEOUT",
    "TOTAL_TIMEOUT_S",
    "Answer",
    "OutboundRefused",
    "Target",
    "check_mcp_url",
    "check_url",
    "deadline",
    "mcp_client",
    "request",
    "request_https",
    "send",
]
