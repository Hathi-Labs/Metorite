"""The ONE test for "is this host a public address" (shared by the gateway and
the email sync).

``gateway/outbound_guard.py`` (URLs that a member supplies) and the IMAP and
SMTP paths (``email_ingestion/providers/imap.py`` and the create route of
``routes/email/transport/accounts.py``) all ask this question. A security test
with two copies misses the next fix, so the rules live here once and
``outbound_guard`` keeps its old names as thin wrappers.

The rules: resolve the host ONCE, unwrap an IPv4-mapped IPv6 address, refuse an
address that is multicast, reserved, not global, site-local, in a range that
carries an IPv4 address, or local to this host. One bad address in a list of
public ones refuses the host. :func:`pinned_public_address` returns the first
checked address, so the caller connects to THAT address and a second DNS answer
(DNS rebinding) never reaches the socket. Fence:
``tests/unit/test_email_webhook_guard.py`` and
``tests/unit/test_public_address.py``.
"""

from __future__ import annotations

import errno
import ipaddress
import socket
from collections.abc import Callable

#: IPv6 ranges that carry an IPv4 address in their low bits. A host can reach
#: the IPv4 address through each one, so the guard refuses the whole range by
#: name, and does not depend on ``is_reserved`` (review round 1). The mapped
#: range ``::ffff:0:0/96`` is unwrapped instead, and its IPv4 address is
#: checked.
IPV4_CARRIERS = tuple(ipaddress.IPv6Network(n) for n in (
    "64:ff9b::/96",      # NAT64, RFC 6052
    "64:ff9b:1::/48",    # local NAT64, RFC 8215
    "::/96",             # IPv4-compatible, deprecated
    "::ffff:0:0:0/96",   # IPv4-translated (SIIT), RFC 2765
))

def resolve(host: str, port: int) -> list[str]:
    """Every address of ``host``, in the order of the resolver.

    The guard calls this ONCE for each check. A test replaces it."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in seen:
            seen.append(addr)
    return seen


def is_local_address(addr: str) -> bool:
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
        return exc.errno not in NO_FAMILY_ERRNOS
    with sock:
        try:
            sock.bind((addr, 0))
        except OSError as exc:
            return exc.errno not in NOT_LOCAL_ERRNOS
    return True


NOT_LOCAL_ERRNOS = frozenset(
    e for e in (errno.EADDRNOTAVAIL, getattr(errno, "WSAEADDRNOTAVAIL", None))
    if e is not None)

NO_FAMILY_ERRNOS = frozenset(
    e for e in (errno.EAFNOSUPPORT, getattr(errno, "WSAEAFNOSUPPORT", None))
    if e is not None)


def address_refusal(
    addresses: list[str],
    is_local: Callable[[str], bool] | None = None,
) -> str | None:
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
                or any(ip in net for net in IPV4_CARRIERS)):
            return "not public"
    for addr in addresses:
        if (is_local or is_local_address)(addr):
            return "local"
    return None




class PublicAddressRefused(Exception):
    """A host does not resolve, or an address of it is not public."""

    def __init__(self, host: str, reason: str) -> None:
        self.host = host
        self.reason = reason
        super().__init__(f"{host[:100]} {reason}")


def pinned_public_address(host: str, port: int) -> str:
    """Resolve ``host`` once and return its FIRST address, or raise.

    Blocking: call it from a worker thread. The caller connects to the address
    that this returns and keeps the host name only for TLS (``server_hostname``).
    """
    try:
        addresses = resolve(host, port)
    except OSError:
        raise PublicAddressRefused(host, "unresolvable") from None
    reason = address_refusal(addresses)
    if reason is not None:
        raise PublicAddressRefused(host, reason)
    return addresses[0]


def host_is_public(host: str) -> bool:
    """True only when each address of ``host`` is public."""
    try:
        pinned_public_address(host, 443)
    except PublicAddressRefused:
        return False
    return True
