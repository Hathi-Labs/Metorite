"""The gateway's ONE guard for a request to a URL that a member supplied.

WS-17 EM-T13b-2, spec ``project-docs/specs/email_app_master_plan.md``
§10.4.15. Two callers use it: the one-click unsubscribe
(``routes/email/automation/senders.py::_http_unsubscribe``) and the rule action
``CALL_WEBHOOK`` (``routes/email/automation/actions.py``). It lives beside
``gateway/db.py`` because it belongs to neither caller, and a security control
with two copies misses the next fix.

The rules, in order:

1. **The check.** The URL uses ``http`` or ``https`` and has a host. It has no
   user name, no password and no backslash. The guard resolves the host ONCE.
   It unwraps an IPv4-mapped IPv6 address. It refuses the URL when any address
   is multicast or is not ``is_global``. So one private address in a list of
   public ones is enough to refuse.
2. **The pin.** The request goes to the FIRST resolved address, with the
   ``Host`` header of the URL. For ``https`` the ``sni_hostname`` extension
   carries the name, so TLS checks the certificate against the name and not
   the address. A second DNS answer (DNS rebinding) can never reach the socket.
   ``trust_env=False``, so no proxy from the environment sees the request.
3. **No redirect.** ``follow_redirects=False``, and a 3xx raises
   :class:`OutboundRefused`. A redirect target never passed the check.
4. **Caps.** 3 seconds to connect and 10 seconds in total. The guard streams
   the answer and keeps at most 64 KiB of it.

A refusal raises :class:`OutboundRefused`, which names the host and a short
reason. It never holds the path or the query of the URL.

``_host_is_public`` and ``_is_safe_external_url`` keep the names that they had
in ``senders.py``, which imports them again.

Fence: ``tests/unit/test_email_webhook_guard.py``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any

import httpx

#: Seconds to open the connection.
CONNECT_TIMEOUT_S = 3.0
#: Seconds for the whole request, the read of the answer included.
TOTAL_TIMEOUT_S = 10.0
#: The most of an answer that the guard reads.
MAX_ANSWER_BYTES = 64 * 1024

_SCHEMES = ("http", "https")


class OutboundRefused(Exception):  # the spec names this class
    """The guard refused a URL, or a 3xx answer, or a run past the time cap.

    ``host`` is the host of the URL and ``reason`` is a short phrase. Neither
    holds the path or the query."""

    def __init__(self, host: str, reason: str) -> None:
        self.host = host
        self.reason = reason
        super().__init__(" ".join(p for p in (host, reason) if p))


@dataclass(frozen=True)
class Target:
    """A URL that passed the check, and the address that it is pinned to.

    Only :func:`check_url` makes one."""

    url: httpx.URL
    host: str
    ip: str


@dataclass(frozen=True)
class Answer:
    """The status of an answer, and at most :data:`MAX_ANSWER_BYTES` of it."""

    status_code: int
    body: bytes

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
        if ip.is_multicast or not ip.is_global:
            return "not public"
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
    return Answer(status_code=resp.status_code, body=body)


async def request(
    method: str, url: str, *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
    json: Any = None,
) -> Answer:
    """:func:`check_url`, then :func:`send`, for one request."""
    target = await check_url(url)
    return await send(target, method, headers=headers, content=content, json=json)


__all__ = [
    "CONNECT_TIMEOUT_S",
    "MAX_ANSWER_BYTES",
    "TOTAL_TIMEOUT_S",
    "Answer",
    "OutboundRefused",
    "Target",
    "check_url",
    "request",
    "send",
]
