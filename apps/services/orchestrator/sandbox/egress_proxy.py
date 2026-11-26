"""The egress proxy of the coding sandbox (WS-43g1, ``maf_coding_engine.md`` §7.3).

One container, ``mtr-egress-proxy``, runs this file with the image's own
``python3 -I``. It runs as uid 65534, with a read-only root, no capability,
no published port and no credential. It is the ONLY way out of a sandbox
container that the broker moved onto its organization's ``--internal``
network after an approval. The broker writes the grants file, and this
process mounts it read-only and reads it again for each request, so a revoke
takes effect at the next request.

**What it refuses (§7.3, fence WS43-F5 ``tests/unit/test_sandbox_egress_proxy.py``):**

1. A request with no valid grant token (HTTP 407). The token rides in the
   ``Proxy-Authorization`` header, as the password of the proxy URL that the
   broker puts in ``HTTP_PROXY`` and ``HTTPS_PROXY``.
2. A host that is not on the grant's list. An entry matches one host
   exactly. An entry that starts with a dot matches the subdomains of the
   rest, at a dot boundary only.
3. A port other than 80 and 443.
4. A host with any resolved address that is not global, and any NAT64, 6to4,
   Teredo or IPv4-mapped address. An IP literal is refused before any lookup.
5. A second DNS answer. The proxy resolves once, checks every address, and
   connects only to an address that it checked.

**The door (done-when 9).** The exact name ``metorite-api.sandbox`` on port
80, plain HTTP only, goes to the ``door_upstream`` of the grants file. That
value is an IP address and a port, so no DNS lookup runs. An empty value, a
``CONNECT``, or a request with no ``X-Sandbox-Capability`` header is refused.
The proxy adds no header. It removes ``Proxy-Authorization`` and the
hop-by-hop headers, and sets ``Connection: close``, so one connection carries
one checked request.

**The log.** One JSON line on stdout per request, allowed or refused, with the
organization, the run, the agent, the approver, the method, the host, the
port, the address, the decision, the reason and the byte counts.

It uses the standard library only, because it runs in the sandbox image.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import datetime as _dt
import hashlib
import hmac
import ipaddress
import json
import re
import selectors
import socket
import socketserver
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

#: The door name of ``agent_sandbox.md`` §12.3.
DOOR_HOST = "metorite-api.sandbox"
#: The one header that the door forward needs (``agent_sandbox.md`` §12.3).
CAPABILITY_HEADER = "x-sandbox-capability"
#: Rule 3.
ALLOWED_PORTS = frozenset({80, 443})
#: The port that the proxy listens on, inside its container.
LISTEN_PORT = 3128

#: Rule 4: the ranges that carry an IPv4 address inside an IPv6 one.
_NAT64 = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)
_SIXTOFOUR = ipaddress.ip_network("2002::/16")
#: The old IPv4-compatible form, ``::a.b.c.d``.
_V4COMPAT = ipaddress.ip_network("::/96")
#: The IPv4-translated form of SIIT, ``::ffff:0:a.b.c.d``.
_V4TRANSLATED = ipaddress.ip_network("::ffff:0:0:0/96")

#: Digits only, ASCII. ``str.isdigit`` takes ``\xb2`` and other digits.
_PORT_RE = re.compile(r"[0-9]{1,5}")
_LENGTH_RE = re.compile(r"[0-9]{1,10}")
#: A control byte other than tab. CR is allowed only as part of CRLF.
_CONTROL_RE = re.compile(rb"[\x00-\x08\x0a-\x1f\x7f-\x9f]")

#: A host name: lower-case letters, digits, dots and hyphens, no empty label.
_HOST_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                      r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$")

_HEAD_MAX = 64 * 1024
_BODY_MAX = 10 * 1024 * 1024
_CHUNK = 65536
#: A tunnel with no byte in either way for this long closes.
TUNNEL_IDLE_S = 300.0
#: The longest life of one tunnel, whatever its traffic (§7.3). It equals the
#: longest grant, so no tunnel outlives the grant that opened it by much.
TUNNEL_MAX_S = 1800.0
#: The whole request head must arrive within this time, so a slow client
#: cannot hold a slot by sending one byte at a time.
HEAD_DEADLINE_S = 10.0
#: The most requests in flight for one grant token, below :data:`MAX_CLIENTS`.
#: So one sandbox cannot take every slot from the others.
MAX_PER_GRANT = 8
#: The most connections from one peer address that have not yet passed the
#: token check. It is checked BEFORE a slot of :data:`MAX_CLIENTS` is taken,
#: so idle connections of one container cannot starve the other orgs.
MAX_PREAUTH_PER_PEER = 4
#: The connect and read timeout of a plain HTTP forward.
FORWARD_TIMEOUT_S = 60.0
#: The most requests in flight. The container's pids limit is 64.
MAX_CLIENTS = 32

#: The hop-by-hop headers that a forward drops (RFC 9110 §7.6.1).
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
})


class Refused(Exception):
    """A refusal, with the HTTP status and the reason for the log line."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        #: The grant of the request, once rule 1 passed. The log line names it.
        self.grant: Grant | None = None


@dataclass(frozen=True)
class Grant:
    """One grant of the file. The broker writes it, keyed by the token's hash."""

    org: str
    run: str
    agent: str
    approver: str
    hosts: tuple[str, ...]
    expires_at: float


@dataclass(frozen=True)
class Target:
    """Where an allowed request goes: a checked address, or the door upstream."""

    host: str
    port: int
    address: str
    connect_port: int
    door: bool = False


@dataclass
class Request:
    """The head of one client request."""

    method: str
    target: str
    version: str
    headers: list[tuple[str, str]] = field(default_factory=list)

    def header(self, name: str) -> str | None:
        name = name.lower()
        for key, value in self.headers:
            if key.lower() == name:
                return value
        return None


# ── the grants file ──────────────────────────────────────────────────────────


def token_key(token: str) -> str:
    """The key of a grant: the SHA-256 of its token. The file holds no token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GrantsDoc:
    """The parsed grants file. A missing or a bad file holds no grant."""

    grants: dict[str, Grant]
    door_upstream: str = ""


def parse_grants(raw: str) -> GrantsDoc:
    """Parse the grants file. Each bad entry is dropped, so it opens nothing."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return GrantsDoc({})
    if not isinstance(data, dict) or not isinstance(data.get("grants"), dict):
        return GrantsDoc({})
    grants: dict[str, Grant] = {}
    for key, entry in data["grants"].items():
        if not (isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key)):
            continue
        if not isinstance(entry, dict):
            continue
        hosts = entry.get("hosts")
        if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
            continue
        try:
            expires = float(entry.get("expires_at"))
        except (TypeError, ValueError):
            continue
        grants[key] = Grant(
            org=str(entry.get("org") or ""),
            run=str(entry.get("run") or ""),
            agent=str(entry.get("agent") or ""),
            approver=str(entry.get("approver") or ""),
            hosts=tuple(h.strip().lower() for h in hosts if h.strip()),
            expires_at=expires,
        )
    door = data.get("door_upstream")
    return GrantsDoc(grants, door if isinstance(door, str) else "")


def load_grants(path: str) -> GrantsDoc:
    """Read the grants file again. Any error reads as no grant."""
    try:
        with open(path, encoding="utf-8") as handle:
            return parse_grants(handle.read(1024 * 1024))
    except OSError:
        return GrantsDoc({})


def token_from(header: str | None) -> str:
    """The grant token of a ``Proxy-Authorization: Basic`` header, or ``""``."""
    if not header:
        return ""
    scheme, _sep, value = header.strip().partition(" ")
    if scheme.lower() != "basic" or not value.strip():
        return ""
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return ""
    _user, sep, password = decoded.partition(":")
    return password if sep else ""


def find_grant(doc: GrantsDoc, token: str, now: float) -> Grant | None:
    """Rule 1: the live grant of *token*, or ``None``."""
    if not token:
        return None
    key = token_key(token)
    for stored, grant in doc.grants.items():
        if hmac.compare_digest(stored, key):
            return grant if grant.expires_at > now else None
    return None


# ── rules 2 to 5 ─────────────────────────────────────────────────────────────


def normalize_host(host: str) -> str:
    """Lower case, one trailing dot dropped. Raises :class:`Refused` on a bad name."""
    value = host.strip().lower()
    if value.endswith("."):
        value = value[:-1]
    if is_ip_literal(value):
        raise Refused(403, "ip_literal")
    if not _HOST_RE.match(value):
        raise Refused(400, "bad_host")
    return value


def is_ip_literal(host: str) -> bool:
    """True for an IPv4 or IPv6 literal, with or without brackets."""
    text = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    text = text.split("%", 1)[0]
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def host_matches(host: str, entries: Sequence[str]) -> bool:
    """Rule 2: an exact entry, or a dot entry at a dot boundary."""
    for entry in entries:
        if entry.startswith("."):
            base = entry[1:]
            if base and host.endswith("." + base):
                return True
        elif host == entry:
            return True
    return False


def address_refusal(address: str) -> str | None:
    """Rule 4: the reason to refuse *address*, or ``None`` when it is global."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return "bad_address"
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return "ipv4_mapped"
        if any(ip in net for net in _NAT64):
            return "nat64"
        if ip in _SIXTOFOUR or ip.sixtofour is not None:
            return "6to4"
        if ip.teredo is not None:
            return "teredo"
        if ip in _V4COMPAT and int(ip) > 1:
            return "ipv4_compatible"
        if ip in _V4TRANSLATED:
            return "ipv4_translated"
    if not ip.is_global:
        return "not_global"
    if ip.is_multicast:
        return "multicast"
    return None


Resolver = Callable[[str, int], Sequence[str]]


def system_resolve(host: str, port: int) -> list[str]:
    """ONE DNS answer: every address of *host*, in order, with no duplicate."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in seen:
            seen.append(address)
    return seen


def parse_upstream(raw: str) -> tuple[str, int] | None:
    """``ip:port`` or ``[ipv6]:port``. A name, or no port, reads as ``None``."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("["):
        host, sep, port = text[1:].partition("]:")
    else:
        host, sep, port = text.rpartition(":")
    if not sep or not _PORT_RE.fullmatch(port):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    number = int(port)
    if not 0 < number < 65536:
        return None
    return host, number


def decide(
    *,
    doc: GrantsDoc,
    token: str,
    method: str,
    host: str,
    port: int,
    capability: bool,
    now: float,
    resolve: Resolver,
) -> tuple[Grant, Target]:
    """Apply rules 1 to 5 and the door rule. Raises :class:`Refused`.

    *resolve* runs at most ONCE, and only for a host that passed rules 1 to
    3. The answer is the address to connect to (rule 5).
    """
    grant = find_grant(doc, token, now)
    if grant is None:
        raise Refused(407, "no_valid_grant")
    try:
        return grant, _check_target(doc, grant, method, host, port, capability, resolve)
    except Refused as refusal:
        refusal.grant = grant
        raise


def _check_target(
    doc: GrantsDoc, grant: Grant, method: str, host: str, port: int, capability: bool,
    resolve: Resolver,
) -> Target:
    """Rules 2 to 5, and the door rule, for a request with a live grant."""
    name = normalize_host(host)
    if port not in ALLOWED_PORTS:
        raise Refused(403, "port")
    if not host_matches(name, grant.hosts):
        raise Refused(403, "host_not_on_grant")
    if name == DOOR_HOST:
        return _door_target(doc, method, port, capability)
    try:
        answer = list(resolve(name, port))
    except (OSError, UnicodeError):
        raise Refused(502, "dns_failed") from None
    if not answer:
        raise Refused(502, "dns_failed")
    for address in answer:
        why = address_refusal(address)
        if why is not None:
            raise Refused(403, f"address_{why}")
    return Target(name, port, answer[0], port)


def _door_target(doc: GrantsDoc, method: str, port: int, capability: bool) -> Target:
    """Done-when 9: the door goes to the upstream address, with no DNS lookup."""
    if not doc.door_upstream.strip():
        raise Refused(403, "door_off")
    upstream = parse_upstream(doc.door_upstream)
    if upstream is None:
        raise Refused(403, "door_upstream_invalid")
    if method == "CONNECT" or port != 80:
        raise Refused(403, "door_plain_http_only")
    if not capability:
        raise Refused(403, "door_no_capability")
    return Target(DOOR_HOST, port, upstream[0], upstream[1], door=True)


# ── the request head ─────────────────────────────────────────────────────────


def parse_head(raw: bytes) -> Request:
    """Parse a request head. Raises :class:`Refused` on a bad one.

    A bare LF, a lone CR, a NUL or any other control byte but tab refuses the
    whole head. Otherwise one header line could hide a second header, such
    as a ``Host`` or a ``Transfer-Encoding``, that the far side reads.
    """
    check_clean(raw)
    try:
        text = raw.decode("iso-8859-1")
    except UnicodeDecodeError:  # pragma: no cover — latin-1 decodes every byte
        raise Refused(400, "bad_request") from None
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise Refused(400, "bad_request")
    request = Request(parts[0].upper(), parts[1], parts[2])
    for line in lines[1:]:
        if not line:
            continue
        key, sep, value = line.partition(":")
        if not sep or not key or key != key.strip():
            raise Refused(400, "bad_header")
        request.headers.append((key, value.strip()))
    return request


def check_clean(raw: bytes) -> None:
    """Refuse a head with a control byte other than tab, or a CR outside CRLF."""
    if _CONTROL_RE.search(raw.replace(b"\r\n", b"")):
        raise Refused(400, "control_byte")


def split_authority(authority: str, default_port: int) -> tuple[str, int]:
    """``host[:port]`` into its parts. Raises :class:`Refused`."""
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise Refused(400, "bad_host")
        host, rest = authority[: end + 1], authority[end + 1 :]
        if rest and not rest.startswith(":"):
            raise Refused(400, "bad_host")
        port_text = rest[1:]
    else:
        host, _sep, port_text = authority.partition(":")
    if not port_text:
        return host, default_port
    if not _PORT_RE.fullmatch(port_text):
        raise Refused(400, "bad_port")
    return host, int(port_text)


def request_target(request: Request) -> tuple[str, int, str]:
    """The host, the port and the origin-form path of a request."""
    if request.method == "CONNECT":
        host, port = split_authority(request.target, 443)
        return host, port, ""
    url = request.target
    if not url.lower().startswith("http://"):
        raise Refused(400, "absolute_http_url_only")
    rest = url[7:]
    slash = rest.find("/")
    authority, path = (rest, "/") if slash < 0 else (rest[:slash], rest[slash:])
    if "@" in authority or not authority:
        raise Refused(400, "bad_host")
    host, port = split_authority(authority, 80)
    return host, port, path


def check_host_header(request: Request, host: str) -> None:
    """A plain HTTP request names one host. Its ``Host`` header must agree.

    Without this, a request for an allowed host could ask the far server,
    by its ``Host`` header, for a site that the grant does not name.
    """
    values = [v for k, v in request.headers if k.lower() == "host"]
    if len(values) > 1:
        raise Refused(400, "host_header_twice")
    if not values:
        return
    named, _port = split_authority(values[0].strip(), 80)
    if normalize_host(named) != normalize_host(host):
        raise Refused(403, "host_header_mismatch")


def forward_head(request: Request, path: str) -> bytes:
    """The head sent upstream. It adds no header of identity or auth."""
    lines = [f"{request.method} {path} {request.version}"]
    for key, value in request.headers:
        if key.lower() in _HOP_BY_HOP:
            continue
        lines.append(f"{key}: {value}")
    lines.append("Connection: close")
    head = "\r\n".join(lines).encode("iso-8859-1")
    check_clean(head)
    return head + b"\r\n\r\n"


# ── the log line ─────────────────────────────────────────────────────────────


def log_line(
    *,
    grant: Grant | None,
    method: str,
    host: str,
    port: int | None,
    address: str,
    decision: str,
    reason: str,
    bytes_up: int = 0,
    bytes_down: int = 0,
    now: float | None = None,
) -> str:
    """One JSON line for one request (§7.3, "What the proxy logs")."""
    stamp = _dt.datetime.fromtimestamp(
        time.time() if now is None else now, tz=_dt.UTC,
    ).isoformat()
    return json.dumps({
        "time": stamp,
        "organization": grant.org if grant else None,
        "run": grant.run if grant else None,
        "agent": grant.agent if grant else None,
        "approver": grant.approver if grant else None,
        "method": method,
        "host": host[:255],
        "port": port,
        "address": address,
        "decision": decision,
        "reason": reason,
        "bytes_up": bytes_up,
        "bytes_down": bytes_down,
    }, sort_keys=True)


# ── the server ───────────────────────────────────────────────────────────────


class Proxy:
    """The request loop. *resolve*, *connect* and *clock* are seams for the fence."""

    def __init__(
        self,
        grants_path: str,
        *,
        resolve: Resolver = system_resolve,
        connect: Callable[[str, int], socket.socket] | None = None,
        clock: Callable[[], float] = time.time,
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self.grants_path = grants_path
        self.resolve = resolve
        self.connect = connect or _connect
        self.clock = clock
        self.emit = emit or _print_line
        self._slots = threading.BoundedSemaphore(MAX_CLIENTS)
        self._per_grant: dict[str, int] = {}
        self._per_grant_lock = threading.Lock()
        self._preauth: dict[str, int] = {}

    def _take_grant_slot(self, key: str) -> bool:
        with self._per_grant_lock:
            held = self._per_grant.get(key, 0)
            if held >= MAX_PER_GRANT:
                return False
            self._per_grant[key] = held + 1
            return True

    def _give_grant_slot(self, key: str) -> None:
        with self._per_grant_lock:
            held = self._per_grant.get(key, 0) - 1
            if held > 0:
                self._per_grant[key] = held
            else:
                self._per_grant.pop(key, None)

    def _take_peer_slot(self, peer: str) -> bool:
        with self._per_grant_lock:
            held = self._preauth.get(peer, 0)
            if held >= MAX_PREAUTH_PER_PEER:
                return False
            self._preauth[peer] = held + 1
            return True

    def _give_peer_slot(self, peer: str) -> None:
        with self._per_grant_lock:
            held = self._preauth.get(peer, 0) - 1
            if held > 0:
                self._preauth[peer] = held
            else:
                self._preauth.pop(peer, None)

    def handle(self, client: socket.socket, peer: str = "") -> None:
        """Serve ONE request on *client*, log it once, and close.

        *peer* is the address of the client. Before the token check, one
        peer holds at most :data:`MAX_PREAUTH_PER_PEER` connections, checked
        before a global slot is taken. Any error that the steps do not handle
        still gets a reply and one ``internal_error`` log line.
        """
        if not self._take_peer_slot(peer):
            with contextlib.suppress(OSError):
                client.sendall(_reply(503, "peer_busy"))
            self.emit(log_line(
                grant=None, method="", host="", port=None, address="",
                decision="refuse", reason="peer_busy",
            ))
            _close(client)
            return
        call = _Call(peer=peer)
        try:
            if not self._slots.acquire(blocking=False):
                with contextlib.suppress(OSError):
                    client.sendall(_reply(503, "busy"))
                return
            try:
                self._handle(client, call)
            except Exception:
                with contextlib.suppress(OSError):
                    client.sendall(_reply(500, "internal_error"))
                self.emit(log_line(
                    grant=call.grant, method=call.method, host=call.host, port=call.port,
                    address="", decision="refuse", reason="internal_error",
                ))
            finally:
                self._slots.release()
        finally:
            self._authed(call)
            _close(client)

    def _authed(self, call: _Call) -> None:
        """Give the pre-auth slot of *call* back, once."""
        if call.preauth:
            call.preauth = False
            self._give_peer_slot(call.peer)

    def _handle(self, client: socket.socket, call: _Call | None = None) -> None:
        call = call if call is not None else _Call(preauth=False)
        method, host, port, grant = "", "", None, None
        try:
            client.settimeout(FORWARD_TIMEOUT_S)
            head, extra = _read_head(client)
            client.settimeout(FORWARD_TIMEOUT_S)
            request = parse_head(head)
            method = call.method = request.method
            host, port, path = request_target(request)
            call.host, call.port = host, port
            token = token_from(request.header("Proxy-Authorization"))
            grant, target = decide(
                doc=load_grants(self.grants_path), token=token, method=method,
                host=host, port=port,
                capability=bool(request.header(CAPABILITY_HEADER)),
                now=self.clock(), resolve=self.resolve,
            )
            call.grant = grant
            self._authed(call)
            if method != "CONNECT":
                try:
                    check_host_header(request, host)
                except Refused as refusal:
                    refusal.grant = grant
                    raise
            key = token_key(token)
            if not self._take_grant_slot(key):
                refusal = Refused(429, "grant_busy")
                refusal.grant = grant
                raise refusal
        except Refused as refusal:
            self._refuse(client, refusal, refusal.grant, method, host, port)
            return
        except OSError:
            self.emit(log_line(
                grant=grant, method=method, host=host, port=port, address="",
                decision="refuse", reason="client_error",
            ))
            return
        try:
            self._serve(client, request, target, grant, path, extra)
        finally:
            self._give_grant_slot(key)

    def _refuse(
        self, client: socket.socket, refusal: Refused, grant: Grant | None,
        method: str, host: str, port: int | None,
    ) -> None:
        with contextlib.suppress(OSError):
            client.sendall(_reply(refusal.status, refusal.reason))
        self.emit(log_line(
            grant=grant, method=method, host=host, port=port, address="",
            decision="refuse", reason=refusal.reason,
        ))

    def _serve(
        self, client: socket.socket, request: Request, target: Target, grant: Grant,
        path: str, extra: bytes,
    ) -> None:
        up = down = 0
        reason = "door" if target.door else "allowed"
        try:
            upstream = self.connect(target.address, target.connect_port)
        except OSError:
            with contextlib.suppress(OSError):
                client.sendall(_reply(502, "upstream_unreachable"))
            self.emit(log_line(
                grant=grant, method=request.method, host=target.host, port=target.port,
                address=target.address, decision="refuse", reason="upstream_unreachable",
            ))
            return
        try:
            if request.method == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                up, down = _tunnel(client, upstream, extra)
            else:
                up, down = _forward(client, upstream, request, path, extra)
        except Refused as refusal:
            reason = refusal.reason
            with contextlib.suppress(OSError):
                client.sendall(_reply(refusal.status, refusal.reason))
        except OSError:
            reason = "stream_error"
        finally:
            with contextlib.suppress(OSError):
                upstream.close()
        self.emit(log_line(
            grant=grant, method=request.method, host=target.host, port=target.port,
            address=target.address, decision="allow", reason=reason,
            bytes_up=up, bytes_down=down,
        ))


@dataclass
class _Call:
    """What one request knows so far. The catch-all log line reads it."""

    peer: str = ""
    preauth: bool = True
    grant: Grant | None = None
    method: str = ""
    host: str = ""
    port: int | None = None


def _close(client: socket.socket) -> None:
    with contextlib.suppress(OSError):
        client.shutdown(socket.SHUT_RDWR)
    with contextlib.suppress(OSError):
        client.close()


def _print_line(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _connect(address: str, port: int) -> socket.socket:
    """Connect to an address that rule 4 checked. No name, so no DNS lookup."""
    return socket.create_connection((address, port), timeout=FORWARD_TIMEOUT_S)


def _reply(status: int, reason: str) -> bytes:
    titles = {
        400: "Bad Request", 403: "Forbidden", 407: "Proxy Authentication Required",
        408: "Request Timeout", 411: "Length Required", 413: "Content Too Large",
        429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway",
        503: "Service Unavailable",
    }
    body = json.dumps({"refused": reason}).encode("utf-8")
    head = [f"HTTP/1.1 {status} {titles.get(status, 'Refused')}",
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
            "Connection: close"]
    if status == 407:
        head.append('Proxy-Authenticate: Basic realm="metorite-egress"')
    return ("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body


def _read_head(client: socket.socket) -> tuple[bytes, bytes]:
    """The request head, and the bytes after it that came in the same reads.

    The whole head must arrive within :data:`HEAD_DEADLINE_S`.
    """
    deadline = time.monotonic() + HEAD_DEADLINE_S
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > _HEAD_MAX:
            raise Refused(400, "head_too_large")
        left = deadline - time.monotonic()
        if left <= 0:
            raise Refused(408, "head_timeout")
        client.settimeout(left)
        try:
            chunk = client.recv(_CHUNK)
        except TimeoutError:
            raise Refused(408, "head_timeout") from None
        if not chunk:
            raise Refused(400, "bad_request")
        buf += chunk
    head, _sep, extra = buf.partition(b"\r\n\r\n")
    return head, extra


def _forward(
    client: socket.socket, upstream: socket.socket, request: Request, path: str,
    extra: bytes,
) -> tuple[int, int]:
    """One plain HTTP request upstream, then the whole answer back."""
    if request.header("Transfer-Encoding"):
        raise Refused(411, "chunked_body")
    if sum(1 for k, _v in request.headers if k.lower() == "content-length") > 1:
        raise Refused(400, "bad_length")
    raw_length = request.header("Content-Length") or "0"
    if not _LENGTH_RE.fullmatch(raw_length):
        raise Refused(400, "bad_length")
    length = int(raw_length)
    if length > _BODY_MAX:
        raise Refused(413, "body_too_large")
    upstream.settimeout(FORWARD_TIMEOUT_S)
    upstream.sendall(forward_head(request, path))
    body = extra[:length]
    sent = len(body)
    if body:
        upstream.sendall(body)
    while sent < length:
        chunk = client.recv(min(_CHUNK, length - sent))
        if not chunk:
            break
        upstream.sendall(chunk)
        sent += len(chunk)
    down = 0
    while True:
        chunk = upstream.recv(_CHUNK)
        if not chunk:
            break
        client.sendall(chunk)
        down += len(chunk)
    return sent, down


def _tunnel(client: socket.socket, upstream: socket.socket, extra: bytes) -> tuple[int, int]:
    """Copy bytes both ways until both sides close, the tunnel is idle, or it
    reaches :data:`TUNNEL_MAX_S`.

    An end of input on one side half-closes the other side, so an answer
    that is still on its way reaches the client.
    """
    up = down = 0
    if extra:
        upstream.sendall(extra)
        up += len(extra)
    for sock in (client, upstream):
        sock.settimeout(None)
    end = time.monotonic() + TUNNEL_MAX_S
    with selectors.DefaultSelector() as sel:
        sel.register(client, selectors.EVENT_READ, upstream)
        sel.register(upstream, selectors.EVENT_READ, client)
        while sel.get_map():
            left = end - time.monotonic()
            if left <= 0:
                break
            events = sel.select(timeout=min(TUNNEL_IDLE_S, left))
            if not events:
                break
            for key, _mask in events:
                source: socket.socket = key.fileobj  # type: ignore[assignment]
                sink: socket.socket = key.data
                chunk = source.recv(_CHUNK)
                if not chunk:
                    sel.unregister(source)
                    with contextlib.suppress(OSError):
                        sink.shutdown(socket.SHUT_WR)
                    continue
                sink.sendall(chunk)
                if source is client:
                    up += len(chunk)
                else:
                    down += len(chunk)
    return up, down


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        peer = str(self.client_address[0]) if self.client_address else ""
        self.server.proxy.handle(self.request, peer)  # type: ignore[attr-defined]


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(grants_path: str, listen: str) -> None:  # pragma: no cover — the container
    host, _sep, port = listen.rpartition(":")
    server = _Server((host or "0.0.0.0", int(port)), _Handler)
    server.proxy = Proxy(grants_path)  # type: ignore[attr-defined]
    _print_line(json.dumps({"event": "egress_proxy.listening", "port": int(port)}))
    server.serve_forever()


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover — the container
    parser = argparse.ArgumentParser(description="The egress proxy of the coding sandbox.")
    parser.add_argument("--grants", required=True)
    parser.add_argument("--listen", default=f"0.0.0.0:{LISTEN_PORT}")
    args = parser.parse_args(argv)
    serve(args.grants, args.listen)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
