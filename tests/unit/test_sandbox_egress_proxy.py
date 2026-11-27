"""WS43-F5 — the egress proxy of the coding sandbox (WS-43g1, task 4009, and
WS-43g2a, task 4070).

Spec ``project-docs/specs/maf_coding_engine.md`` §7.3 ("What the proxy
refuses", "What the proxy logs", "The door forward", "The door wait"),
done-when 1, 2, 9 and 13 of WS-43g, and §10 WS43-F5. WS-43g2a: the door read
waits ``--door-read-timeout``, every other host keeps 60 s, and the proxy
refuses to start below 90 s.

What breaks this fence: the proxy allows an address with
``is_global == False``, a NAT64, 6to4 or IPv4-mapped address, a host off the
list, ``upload.pypi.org``, ``evilexample.org`` for ``.example.org``, a request
with no token, or a second DNS answer. Or it drops the log line or its
approver field. Or the door forward does a DNS lookup, adds a header, passes
``Proxy-Authorization`` on, or answers while ``door_upstream`` is empty.

The proxy runs in the sandbox image, so these tests load it by its path. Each
test here is hermetic: a fake resolver and a socket pair stand in for DNS
and the upstream. The Docker half (a real proxy, a real door stub and
``pypi.org``) lives in ``test_sandbox_network_grant.py`` (WS43-F9), because
it needs the broker's grant.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PROXY_PATH = _REPO / "apps" / "services" / "orchestrator" / "sandbox" / "egress_proxy.py"

TOKEN = "t0k3n-" + "x" * 37
NOW = 1_800_000_000.0
#: A hang guard for each socket test, never a budget.
HANG_S = 20.0


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ws43g1_egress_proxy", _PROXY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ep = _load()


def _grant(hosts: list[str], *, expires: float = NOW + 600) -> dict[str, Any]:
    return {
        "org": "org-a", "run": "run-1", "agent": "agent-x",
        "approver": "member@example.com", "hosts": hosts, "expires_at": expires,
    }


def _doc(hosts: list[str], *, door: str = "", expires: float = NOW + 600) -> Any:
    raw = json.dumps({
        "version": 1, "door_upstream": door,
        "grants": {ep.token_key(TOKEN): _grant(hosts, expires=expires)},
    })
    return ep.parse_grants(raw)


class Resolver:
    """A fake DNS. Each call gives the next answer. It counts the calls."""

    def __init__(self, *answers: list[str]) -> None:
        self.answers = list(answers) or [["93.184.216.34"]]
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        return self.answers[min(len(self.calls), len(self.answers)) - 1]


def _decide(
    hosts: list[str], host: str, port: int = 443, *, method: str = "CONNECT",
    token: str = TOKEN, resolve: Resolver | None = None, door: str = "",
    capability: bool = False, expires: float = NOW + 600,
) -> tuple[Any, Any]:
    return ep.decide(
        doc=_doc(hosts, door=door, expires=expires), token=token, method=method, host=host,
        port=port, capability=capability, now=NOW, resolve=resolve or Resolver(),
    )


def _refusal(*args: Any, **kwargs: Any) -> Any:
    with pytest.raises(ep.Refused) as caught:
        _decide(*args, **kwargs)
    return caught.value


# ── rule 1: the grant token ──────────────────────────────────────────────────


def test_a_request_with_no_token_gets_407() -> None:
    refusal = _refusal(["pypi.org"], "pypi.org", token="")
    assert (refusal.status, refusal.reason) == (407, "no_valid_grant")
    assert refusal.grant is None


def test_a_wrong_token_gets_407() -> None:
    assert _refusal(["pypi.org"], "pypi.org", token="other").status == 407


def test_an_expired_grant_gets_407() -> None:
    assert _refusal(["pypi.org"], "pypi.org", expires=NOW - 1).status == 407


def test_the_file_holds_the_hash_of_the_token_and_never_the_token() -> None:
    doc = _doc(["pypi.org"])
    assert TOKEN not in json.dumps(list(doc.grants))
    assert ep.find_grant(doc, TOKEN, NOW) is not None
    assert ep.find_grant(doc, ep.token_key(TOKEN), NOW) is None, "the hash is not a token"


@pytest.mark.parametrize("header", [
    None, "", "Bearer abc", "Basic", "Basic !!!not-base64!!!",
    "Basic " + base64.b64encode(b"no-colon").decode(),
])
def test_a_bad_proxy_authorization_header_gives_no_token(header: str | None) -> None:
    assert ep.token_from(header) == ""


def test_the_token_is_the_password_of_the_proxy_url() -> None:
    header = "Basic " + base64.b64encode(f"grant:{TOKEN}".encode()).decode()
    assert ep.token_from(header) == TOKEN


@pytest.mark.parametrize("raw", [
    "", "not json", "[]", '{"grants": []}',
    '{"grants": {"short": {"hosts": ["pypi.org"], "expires_at": 9e12}}}',
])
def test_a_bad_grants_file_holds_no_grant(raw: str) -> None:
    assert ep.parse_grants(raw).grants == {}


def test_a_missing_grants_file_holds_no_grant(tmp_path: Path) -> None:
    assert ep.load_grants(str(tmp_path / "absent.json")).grants == {}


# ── rule 2: the host list ────────────────────────────────────────────────────


@pytest.mark.parametrize(("entries", "host"), [
    (["pypi.org"], "upload.pypi.org"),
    (["pypi.org"], "evil.com"),
    (["pypi.org"], "pypi.org.evil.com"),
    ([".example.org"], "evilexample.org"),
    ([".example.org"], "example.org"),
    (["files.pythonhosted.org"], "pythonhosted.org"),
])
def test_a_host_off_the_list_is_refused(entries: list[str], host: str) -> None:
    resolve = Resolver()
    refusal = _refusal(entries, host, resolve=resolve)
    assert (refusal.status, refusal.reason) == (403, "host_not_on_grant")
    assert resolve.calls == [], "a host off the list must not reach DNS"
    assert refusal.grant is not None and refusal.grant.approver == "member@example.com"


@pytest.mark.parametrize(("entries", "host"), [
    (["pypi.org"], "pypi.org"),
    (["pypi.org"], "PyPI.org."),
    ([".example.org"], "a.example.org"),
    ([".example.org"], "a.b.example.org"),
])
def test_a_host_on_the_list_passes(entries: list[str], host: str) -> None:
    grant, target = _decide(entries, host)
    assert target.address == "93.184.216.34" and grant.run == "run-1"


# ── rule 3: the ports ────────────────────────────────────────────────────────


@pytest.mark.parametrize("port", [22, 25, 53, 3128, 8080, 8443, 65535])
def test_a_port_other_than_80_and_443_is_refused(port: int) -> None:
    resolve = Resolver()
    assert _refusal(["pypi.org"], "pypi.org", port, resolve=resolve).reason == "port"
    assert resolve.calls == []


@pytest.mark.parametrize("port", [80, 443])
def test_ports_80_and_443_pass(port: int) -> None:
    assert _decide(["pypi.org"], "pypi.org", port)[1].port == port


# ── rule 4: the addresses ────────────────────────────────────────────────────


@pytest.mark.parametrize(("address", "why"), [
    ("127.0.0.1", "not_global"),
    ("10.1.2.3", "not_global"),
    ("172.17.0.1", "not_global"),
    ("192.168.1.1", "not_global"),
    ("169.254.169.254", "not_global"),
    ("100.64.0.1", "not_global"),
    ("0.0.0.0", "not_global"),
    ("224.0.0.1", "multicast"),
    ("239.1.2.3", "multicast"),
    ("ff02::1", "multicast"),
    ("::1", "not_global"),
    ("fe80::1", "not_global"),
    ("fc00::1", "not_global"),
    ("fd00:ec2::254", "not_global"),
    ("::ffff:8.8.8.8", "ipv4_mapped"),
    ("::ffff:127.0.0.1", "ipv4_mapped"),
    ("64:ff9b::808:808", "nat64"),
    ("64:ff9b::a00:1", "nat64"),
    ("64:ff9b:1::a00:1", "nat64"),
    ("2002:808:808::1", "6to4"),
    ("2002:a00:1::1", "6to4"),
    ("2001:0:4136:e378:8000:63bf:3fff:fdd2", "teredo"),
])
def test_an_address_that_is_not_global_is_refused(address: str, why: str) -> None:
    refusal = _refusal(["pypi.org"], "pypi.org", resolve=Resolver([address]))
    assert (refusal.status, refusal.reason) == (403, f"address_{why}")
    assert refusal.grant is not None, "the log line of a rule 4 refusal names the grant"


def test_one_bad_address_among_good_ones_refuses_the_host() -> None:
    resolve = Resolver(["151.101.0.223", "10.0.0.5", "151.101.64.223"])
    assert _refusal(["pypi.org"], "pypi.org", resolve=resolve).reason == "address_not_global"


@pytest.mark.parametrize("host", [
    "127.0.0.1", "8.8.8.8", "169.254.169.254", "[::1]", "[2606:4700::1111]", "::1",
])
def test_an_ip_literal_is_refused_before_any_lookup(host: str) -> None:
    resolve = Resolver()
    refusal = _refusal(["pypi.org", host], host, resolve=resolve)
    assert refusal.reason == "ip_literal"
    assert resolve.calls == []


@pytest.mark.parametrize("address", ["151.101.0.223", "2a04:4e42::223"])
def test_a_global_address_passes(address: str) -> None:
    assert _decide(["pypi.org"], "pypi.org", resolve=Resolver([address]))[1].address == address


# ── rule 5: one DNS answer ───────────────────────────────────────────────────


def test_the_proxy_resolves_once_and_keeps_the_address_it_checked() -> None:
    """A DNS rebind gives a public answer first and a private one second."""
    resolve = Resolver(["151.101.0.223"], ["127.0.0.1"])
    _, target = _decide(["pypi.org"], "pypi.org", resolve=resolve)
    assert resolve.calls == [("pypi.org", 443)], "exactly one DNS answer"
    assert target.address == "151.101.0.223"


def test_a_failed_lookup_is_refused() -> None:
    def boom(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    refusal = _refusal(["pypi.org"], "pypi.org", resolve=boom)  # type: ignore[arg-type]
    assert (refusal.status, refusal.reason) == (502, "dns_failed")


# ── done-when 9: the door ────────────────────────────────────────────────────


def test_the_door_goes_to_the_upstream_with_no_dns_lookup() -> None:
    resolve = Resolver()
    _, target = _decide(
        [ep.DOOR_HOST], ep.DOOR_HOST, 80, method="GET", door="10.20.0.5:8000",
        capability=True, resolve=resolve,
    )
    assert resolve.calls == [], "the door does no DNS lookup"
    assert (target.address, target.connect_port, target.door) == ("10.20.0.5", 8000, True)


def test_the_door_with_an_ipv6_upstream() -> None:
    _, target = _decide(
        [ep.DOOR_HOST], ep.DOOR_HOST, 80, method="GET", door="[fd00::5]:8000",
        capability=True,
    )
    assert (target.address, target.connect_port) == ("fd00::5", 8000)


@pytest.mark.parametrize(("door", "reason"), [
    ("", "door_off"),
    ("   ", "door_off"),
    ("gateway.internal:8000", "door_upstream_invalid"),
    ("10.20.0.5", "door_upstream_invalid"),
    ("10.20.0.5:0", "door_upstream_invalid"),
    ("10.20.0.5:99999", "door_upstream_invalid"),
])
def test_the_door_refuses_with_no_upstream_address(door: str, reason: str) -> None:
    resolve = Resolver()
    refusal = _refusal(
        [ep.DOOR_HOST], ep.DOOR_HOST, 80, method="GET", door=door, capability=True,
        resolve=resolve,
    )
    assert refusal.reason == reason
    assert resolve.calls == []


def test_the_door_refuses_a_connect_and_port_443() -> None:
    kwargs = {"door": "10.20.0.5:8000", "capability": True}
    assert _refusal([ep.DOOR_HOST], ep.DOOR_HOST, 80, method="CONNECT", **kwargs).reason == (
        "door_plain_http_only"
    )
    assert _refusal([ep.DOOR_HOST], ep.DOOR_HOST, 443, method="GET", **kwargs).reason == (
        "door_plain_http_only"
    )


def test_the_door_refuses_a_request_with_no_capability_header() -> None:
    refusal = _refusal(
        [ep.DOOR_HOST], ep.DOOR_HOST, 80, method="GET", door="10.20.0.5:8000",
    )
    assert refusal.reason == "door_no_capability"


def test_the_door_needs_the_door_on_the_grant() -> None:
    refusal = _refusal(
        ["pypi.org"], ep.DOOR_HOST, 80, method="GET", door="10.20.0.5:8000", capability=True,
    )
    assert refusal.reason == "host_not_on_grant"


def test_only_the_exact_door_name_is_forwarded() -> None:
    """A look-alike name is an ordinary host: it needs DNS and a global address."""
    resolve = Resolver(["10.20.0.5"])
    refusal = _refusal(
        ["x.metorite-api.sandbox"], "x.metorite-api.sandbox", 80, method="GET",
        door="10.20.0.5:8000", capability=True, resolve=resolve,
    )
    assert refusal.reason == "address_not_global"


def test_the_forward_head_adds_no_header_and_drops_the_proxy_token() -> None:
    request = ep.parse_head(
        b"GET http://metorite-api.sandbox/crm/leads?page=1 HTTP/1.1\r\n"
        b"Host: metorite-api.sandbox\r\n"
        b"Proxy-Authorization: Basic Z3JhbnQ6dG9rZW4=\r\n"
        b"Proxy-Connection: keep-alive\r\n"
        b"Connection: keep-alive\r\n"
        b"X-Sandbox-Capability: msbx_abc\r\n"
        b"Accept: application/json"
    )
    _host, _port, path = ep.request_target(request)
    head = ep.forward_head(request, path).decode("iso-8859-1")
    lines = head.split("\r\n")
    assert lines[0] == "GET /crm/leads?page=1 HTTP/1.1"
    names = [line.split(":", 1)[0].lower() for line in lines[1:] if line]
    assert names == ["host", "x-sandbox-capability", "accept", "connection"]
    assert "Connection: close" in lines
    assert "Proxy-Authorization" not in head and "Z3JhbnQ6" not in head


# ── the request head ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    b"GET / HTTP/1.1\r\nHost: pypi.org",
    b"GET https://pypi.org/ HTTP/1.1\r\nHost: pypi.org",
    b"GET http://user@pypi.org/ HTTP/1.1\r\nHost: pypi.org",
    b"GET http:///x HTTP/1.1\r\nHost: pypi.org",
])
def test_a_request_that_is_not_absolute_http_is_refused(raw: bytes) -> None:
    with pytest.raises(ep.Refused):
        ep.request_target(ep.parse_head(raw))


@pytest.mark.parametrize("raw", [b"GARBAGE", b"GET / SMTP/1.0", b"GET http://a/ HTTP/1.1\r\nbad"])
def test_a_bad_head_is_refused(raw: bytes) -> None:
    with pytest.raises(ep.Refused):
        ep.parse_head(raw)


# ── the server: one request, one log line ────────────────────────────────────


class Upstream:
    """The far side of a socket pair, played by a thread."""

    def __init__(self, serve: Callable[[socket.socket], None]) -> None:
        self.near, self.far = socket.socketpair()
        self.received = b""
        self.thread = threading.Thread(target=self._run, args=(serve,), daemon=True)
        self.thread.start()

    def _run(self, serve: Callable[[socket.socket], None]) -> None:
        self.far.settimeout(HANG_S)
        try:
            serve(self.far)
        finally:
            self.far.close()


def _http_upstream(body: bytes) -> Callable[[socket.socket], None]:
    def serve(sock: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        serve.received = data  # type: ignore[attr-defined]
        sock.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n" + body
        )

    return serve


def _echo_upstream(sock: socket.socket) -> None:
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        sock.sendall(chunk.upper())


@pytest.fixture
def proxy_env(tmp_path: Path) -> Iterator[dict[str, Any]]:
    grants = tmp_path / "grants.json"
    lines: list[str] = []
    connects: list[tuple[str, int]] = []
    state: dict[str, Any] = {"lines": lines, "connects": connects, "grants": grants}

    def write(hosts: list[str], door: str = "") -> None:
        grants.write_text(json.dumps({
            "version": 1, "door_upstream": door,
            "grants": {ep.token_key(TOKEN): _grant(hosts)},
        }), encoding="utf-8")

    def connect(address: str, port: int) -> socket.socket:
        connects.append((address, port))
        return state["upstream"].near

    state["write"] = write
    state["resolver"] = Resolver(["151.101.0.223"], ["127.0.0.1"])
    state["proxy"] = ep.Proxy(
        str(grants), resolve=state["resolver"], connect=connect, clock=lambda: NOW,
        emit=lines.append,
    )
    yield state


def _auth() -> bytes:
    return b"Proxy-Authorization: Basic " + base64.b64encode(f"grant:{TOKEN}".encode()) + b"\r\n"


def _exchange(proxy: Any, payload: bytes, *, after: bytes = b"") -> bytes:
    client, server = socket.socketpair()
    client.settimeout(HANG_S)
    worker = threading.Thread(target=proxy.handle, args=(server,), daemon=True)
    worker.start()
    try:
        client.sendall(payload)
        data = b""
        if after:
            data = client.recv(4096)
            client.sendall(after)
        client.shutdown(socket.SHUT_WR)
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        client.close()
        worker.join(HANG_S)
        server.close()
    assert not worker.is_alive(), "the proxy hung on one request"
    return data


def test_a_refusal_writes_one_log_line_with_the_approver(proxy_env: dict[str, Any]) -> None:
    proxy_env["write"](["pypi.org"])
    reply = _exchange(
        proxy_env["proxy"], b"CONNECT evil.com:443 HTTP/1.1\r\nHost: evil.com:443\r\n" + _auth()
        + b"\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 403")
    assert len(proxy_env["lines"]) == 1
    line = json.loads(proxy_env["lines"][0])
    assert line["decision"] == "refuse" and line["reason"] == "host_not_on_grant"
    assert line["approver"] == "member@example.com"
    assert (line["organization"], line["run"], line["agent"]) == ("org-a", "run-1", "agent-x")
    assert (line["method"], line["host"], line["port"]) == ("CONNECT", "evil.com", 443)
    assert set(line) == {
        "time", "organization", "run", "agent", "approver", "method", "host", "port",
        "address", "decision", "reason", "bytes_up", "bytes_down",
    }
    assert proxy_env["connects"] == []


def test_no_token_gets_407_and_one_log_line(proxy_env: dict[str, Any]) -> None:
    proxy_env["write"](["pypi.org"])
    reply = _exchange(proxy_env["proxy"], b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 407") and b"Proxy-Authenticate: Basic" in reply
    line = json.loads(proxy_env["lines"][0])
    assert (line["decision"], line["reason"], line["approver"]) == (
        "refuse", "no_valid_grant", None,
    )
    assert len(proxy_env["lines"]) == 1


def test_a_tunnel_connects_to_the_checked_address_and_logs_once(
    proxy_env: dict[str, Any],
) -> None:
    proxy_env["write"](["pypi.org"])
    proxy_env["upstream"] = Upstream(_echo_upstream)
    reply = _exchange(
        proxy_env["proxy"],
        b"CONNECT pypi.org:443 HTTP/1.1\r\nHost: pypi.org:443\r\n" + _auth() + b"\r\nhello",
        after=b" world",
    )
    assert reply.startswith(b"HTTP/1.1 200 Connection established\r\n\r\n")
    assert reply.endswith(b"HELLO WORLD"), reply
    assert proxy_env["connects"] == [("151.101.0.223", 443)], "the checked address, by IP"
    assert proxy_env["resolver"].calls == [("pypi.org", 443)], "one DNS answer"
    assert len(proxy_env["lines"]) == 1
    line = json.loads(proxy_env["lines"][0])
    assert (line["decision"], line["reason"], line["address"]) == (
        "allow", "allowed", "151.101.0.223",
    )
    assert line["approver"] == "member@example.com" and line["bytes_up"] == len(b"hello world")


def test_the_door_forwards_to_the_upstream_with_no_proxy_token(
    proxy_env: dict[str, Any],
) -> None:
    proxy_env["write"]([ep.DOOR_HOST], door="10.20.0.5:8000")
    serve = _http_upstream(b'{"ok":true}')
    proxy_env["upstream"] = Upstream(serve)
    reply = _exchange(
        proxy_env["proxy"],
        b"GET http://metorite-api.sandbox/crm/leads HTTP/1.1\r\n"
        b"Host: metorite-api.sandbox\r\nX-Sandbox-Capability: msbx_abc\r\n" + _auth() + b"\r\n",
    )
    proxy_env["upstream"].thread.join(HANG_S)
    assert reply.startswith(b"HTTP/1.1 200 OK") and reply.endswith(b'{"ok":true}')
    assert proxy_env["connects"] == [("10.20.0.5", 8000)]
    assert proxy_env["resolver"].calls == [], "the door does no DNS lookup"
    sent = serve.received.decode("iso-8859-1")  # type: ignore[attr-defined]
    assert sent.startswith("GET /crm/leads HTTP/1.1\r\n")
    assert "Proxy-Authorization" not in sent and TOKEN not in sent
    line = json.loads(proxy_env["lines"][0])
    assert (line["decision"], line["reason"], line["host"]) == ("allow", "door", ep.DOOR_HOST)
    assert len(proxy_env["lines"]) == 1


def test_the_door_is_refused_while_the_upstream_is_empty(proxy_env: dict[str, Any]) -> None:
    proxy_env["write"]([ep.DOOR_HOST], door="")
    reply = _exchange(
        proxy_env["proxy"],
        b"GET http://metorite-api.sandbox/crm/leads HTTP/1.1\r\n"
        b"Host: metorite-api.sandbox\r\nX-Sandbox-Capability: msbx_abc\r\n" + _auth() + b"\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 403")
    assert proxy_env["connects"] == []
    assert json.loads(proxy_env["lines"][0])["reason"] == "door_off"


def test_a_revoke_takes_effect_at_the_next_request(proxy_env: dict[str, Any]) -> None:
    """The proxy reads the grants file again for each request."""
    proxy_env["write"](["pypi.org"])
    proxy_env["upstream"] = Upstream(_echo_upstream)
    assert _exchange(
        proxy_env["proxy"], b"CONNECT pypi.org:443 HTTP/1.1\r\n" + _auth() + b"\r\n",
    ).startswith(b"HTTP/1.1 200")
    proxy_env["grants"].write_text('{"version": 1, "grants": {}}', encoding="utf-8")
    assert _exchange(
        proxy_env["proxy"], b"CONNECT pypi.org:443 HTTP/1.1\r\n" + _auth() + b"\r\n",
    ).startswith(b"HTTP/1.1 407")
    assert [json.loads(x)["decision"] for x in proxy_env["lines"]] == ["allow", "refuse"]


def test_a_host_header_that_names_another_site_is_refused(proxy_env: dict[str, Any]) -> None:
    """No fronting: an allowed host in the URL with another site in ``Host``."""
    proxy_env["write"](["pypi.org"])
    reply = _exchange(
        proxy_env["proxy"],
        b"GET http://pypi.org/ HTTP/1.1\r\nHost: evil.example\r\n" + _auth() + b"\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 403")
    assert proxy_env["connects"] == []
    line = json.loads(proxy_env["lines"][0])
    assert line["reason"] == "host_header_mismatch" and line["approver"] == "member@example.com"


def test_a_chunked_body_is_refused(proxy_env: dict[str, Any]) -> None:
    proxy_env["write"](["pypi.org"])
    proxy_env["upstream"] = Upstream(lambda sock: None)
    reply = _exchange(
        proxy_env["proxy"],
        b"POST http://pypi.org/x HTTP/1.1\r\nHost: pypi.org\r\nTransfer-Encoding: chunked\r\n"
        + _auth() + b"\r\n0\r\n\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 411")
    assert json.loads(proxy_env["lines"][0])["reason"] == "chunked_body"


def test_the_proxy_uses_only_the_standard_library() -> None:
    """It runs in the sandbox image with ``python3 -I``: no third-party import."""
    import ast

    tree = ast.parse(_PROXY_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names) | {"__future__"}, names - set(
        sys.stdlib_module_names
    )


def test_the_proxy_reads_no_environment_value() -> None:
    """Its inputs are its arguments and the grants file. It holds no credential."""
    text = _PROXY_PATH.read_text(encoding="utf-8")
    assert "os.environ" not in text and "getenv" not in text


# ── review round 1 (2026-11-26) ──────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\nX-A: v\nHost: evil.com\nTransfer-Encoding: chunked",
    b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\nX-A: v\rHost: evil.com",
    b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\nX-A: v\x00w",
    b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\nX-A: v\x1bw",
    b"GET http://pypi.org/\x7f HTTP/1.1\r\nHost: pypi.org",
    b"GET http://pypi.org/ HTTP/1.1\nHost: pypi.org",
])
def test_a_control_byte_in_the_head_is_refused(raw: bytes) -> None:
    with pytest.raises(ep.Refused) as caught:
        ep.parse_head(raw)
    assert caught.value.reason == "control_byte"


def test_a_tab_in_a_header_value_passes() -> None:
    request = ep.parse_head(b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\nX-A: a\tb")
    assert request.header("X-A") == "a\tb"


def test_a_smuggled_header_never_reaches_the_upstream(proxy_env: dict[str, Any]) -> None:
    proxy_env["write"](["pypi.org"])
    proxy_env["upstream"] = Upstream(lambda sock: None)
    reply = _exchange(
        proxy_env["proxy"],
        b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\nX-A: v\nHost: evil.com\n"
        b"Transfer-Encoding: chunked\r\n" + _auth() + b"\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 400") and proxy_env["connects"] == []
    assert json.loads(proxy_env["lines"][0])["reason"] == "control_byte"


@pytest.mark.parametrize("port", ["\xb2", "4\u0663", "\uff11", "+80", " 80", "123456"])
def test_a_port_that_is_not_ascii_digits_is_refused(port: str) -> None:
    with pytest.raises(ep.Refused) as caught:
        ep.split_authority(f"pypi.org:{port}", 443)
    assert caught.value.reason == "bad_port"
    assert ep.parse_upstream(f"10.0.0.5:{port}") is None


def test_a_content_length_that_is_not_ascii_digits_gets_a_reply_and_a_line(
    proxy_env: dict[str, Any],
) -> None:
    proxy_env["write"](["pypi.org"])
    proxy_env["upstream"] = Upstream(lambda sock: None)
    reply = _exchange(
        proxy_env["proxy"],
        "GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\nContent-Length: \xb2\r\n".encode(
            "iso-8859-1"
        ) + _auth() + b"\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 400")
    assert [json.loads(x)["reason"] for x in proxy_env["lines"]] == ["bad_length"]


def test_an_unexpected_error_still_gets_a_reply_and_one_line(
    proxy_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_: Any, **__: Any) -> Any:
        raise ValueError("unexpected")

    monkeypatch.setattr(ep, "request_target", boom)
    proxy_env["write"](["pypi.org"])
    reply = _exchange(proxy_env["proxy"], b"CONNECT pypi.org:443 HTTP/1.1\r\n" + _auth() + b"\r\n")
    assert reply.startswith(b"HTTP/1.1 500")
    (line,) = proxy_env["lines"]
    assert json.loads(line)["reason"] == "internal_error"


def test_a_slow_head_ends_at_the_deadline(
    proxy_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ep, "HEAD_DEADLINE_S", 0.5)
    client, server = socket.socketpair()
    worker = threading.Thread(target=proxy_env["proxy"].handle, args=(server,), daemon=True)
    worker.start()
    try:
        for byte in b"CONNECT pypi.org:443 HTTP/1.1\r\n":
            try:
                client.sendall(bytes([byte]))
            except OSError:
                break  # the proxy closed at the deadline
            if not worker.is_alive():
                break
            worker.join(0.05)
        worker.join(HANG_S)
        assert not worker.is_alive(), "a slow head held its slot past the deadline"
    finally:
        client.close()
    assert json.loads(proxy_env["lines"][0])["reason"] == "head_timeout"


def test_one_grant_holds_at_most_its_share_of_slots(
    proxy_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ep, "MAX_PER_GRANT", 1)
    proxy = proxy_env["proxy"]
    assert proxy._take_grant_slot(ep.token_key(TOKEN))
    proxy_env["write"](["pypi.org"])
    reply = _exchange(proxy, b"CONNECT pypi.org:443 HTTP/1.1\r\n" + _auth() + b"\r\n")
    assert reply.startswith(b"HTTP/1.1 429")
    line = json.loads(proxy_env["lines"][0])
    assert line["reason"] == "grant_busy" and line["approver"] == "member@example.com"
    assert proxy_env["connects"] == []
    proxy._give_grant_slot(ep.token_key(TOKEN))
    assert proxy._per_grant == {}
    assert ep.MAX_PER_GRANT < ep.MAX_CLIENTS or ep.MAX_PER_GRANT == 1


def test_the_share_of_one_grant_is_below_the_box_slots() -> None:
    assert 0 < _load().MAX_PER_GRANT < ep.MAX_CLIENTS


def test_a_busy_tunnel_ends_at_its_lifetime(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tunnel that never goes idle still ends at ``TUNNEL_MAX_S``."""
    monkeypatch.setattr(ep, "TUNNEL_MAX_S", 0.5)
    client, client_far = socket.socketpair()
    upstream, upstream_far = socket.socketpair()
    stop = threading.Event()

    def chatter() -> None:
        while not stop.is_set():
            try:
                upstream_far.sendall(b"x")
            except OSError:
                return
            stop.wait(0.01)

    talker = threading.Thread(target=chatter, daemon=True)
    talker.start()
    def drain_loop() -> None:
        while not stop.is_set():
            try:
                if not client.recv(65536):
                    return
            except OSError:
                return

    drain = threading.Thread(target=drain_loop, daemon=True)
    drain.start()
    result: list[tuple[int, int]] = []
    worker = threading.Thread(
        target=lambda: result.append(ep._tunnel(client_far, upstream, b"")), daemon=True,
    )
    worker.start()
    worker.join(HANG_S)
    stop.set()
    for sock in (client, client_far, upstream, upstream_far):
        sock.close()
    assert not worker.is_alive(), "the tunnel outlived TUNNEL_MAX_S"
    assert result and result[0][1] > 0


def test_the_tunnel_lifetime_is_the_longest_grant() -> None:
    assert ep.TUNNEL_MAX_S == 1800.0


@pytest.mark.parametrize("address", ["::808:808", "::a00:1", "::7f00:1"])
def test_an_ipv4_compatible_address_is_refused(address: str) -> None:
    refusal = _refusal(["pypi.org"], "pypi.org", resolve=Resolver([address]))
    assert refusal.reason in {"address_ipv4_compatible", "address_not_global"}
    assert ep.address_refusal("::808:808") == "ipv4_compatible"


@pytest.mark.parametrize("address", ["::ffff:0:a00:1", "::ffff:0:808:808", "::ffff:0:7f00:1"])
def test_an_ipv4_translated_address_is_refused(address: str) -> None:
    assert ep.address_refusal(address) == "ipv4_translated"
    refusal = _refusal(["pypi.org"], "pypi.org", resolve=Resolver([address]))
    assert refusal.reason == "address_ipv4_translated"


# ── review round 2 (2026-11-26) ──────────────────────────────────────────────


def test_a_c1_control_byte_is_refused() -> None:
    with pytest.raises(ep.Refused) as caught:
        ep.parse_head(b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\nX-A: v\x85w")
    assert caught.value.reason == "control_byte"


def test_a_full_proxy_refuses_with_one_log_line(
    proxy_env: dict[str, Any],
) -> None:
    """Done-when 2: a refusal because every slot of the proxy is taken still
    writes one log line. Mutation: drop the emit of the busy path, and this
    test fails."""
    proxy = proxy_env["proxy"]
    proxy._slots = threading.BoundedSemaphore(1)
    assert proxy._slots.acquire(blocking=False)
    try:
        before = len(proxy_env["lines"])
        client, server = socket.socketpair()
        client.settimeout(HANG_S)
        proxy.handle(server, "10.0.0.9")
        assert client.recv(4096).startswith(b"HTTP/1.1 503")
        client.close()
        new = proxy_env["lines"][before:]
        assert len(new) == 1
        assert json.loads(new[0])["reason"] == "busy"
        assert proxy._preauth == {}
    finally:
        proxy._slots.release()


def test_one_peer_holds_at_most_its_preauth_share(
    proxy_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle connections of one peer cannot take the slots of another peer."""
    proxy = proxy_env["proxy"]
    idle: list[tuple[socket.socket, threading.Thread]] = []
    try:
        for _ in range(ep.MAX_PREAUTH_PER_PEER):
            client, server = socket.socketpair()
            worker = threading.Thread(
                target=proxy.handle, args=(server, "10.0.0.7"), daemon=True,
            )
            worker.start()
            idle.append((client, worker))
        deadline = time.monotonic() + HANG_S
        while proxy._preauth.get("10.0.0.7") != ep.MAX_PREAUTH_PER_PEER:
            assert time.monotonic() < deadline, "the idle connections never took a slot"
            time.sleep(0.01)
        # A fifth idle connection of the same peer is refused at once.
        client, server = socket.socketpair()
        client.settimeout(HANG_S)
        proxy.handle(server, "10.0.0.7")
        assert client.recv(4096).startswith(b"HTTP/1.1 503")
        client.close()
        assert json.loads(proxy_env["lines"][-1])["reason"] == "peer_busy"
        # Another peer still gets a slot: its request reaches the token check.
        proxy_env["write"](["pypi.org"])
        other, other_server = socket.socketpair()
        other.settimeout(HANG_S)
        worker = threading.Thread(
            target=proxy.handle, args=(other_server, "10.0.0.8"), daemon=True,
        )
        worker.start()
        other.sendall(b"CONNECT pypi.org:443 HTTP/1.1\r\n\r\n")
        assert other.recv(4096).startswith(b"HTTP/1.1 407")
        worker.join(HANG_S)
        other.close()
    finally:
        for client, worker in idle:
            client.close()
            worker.join(HANG_S)
    assert proxy._preauth == {}, "every pre-auth slot comes back"


def test_requests_one_after_another_never_run_out_of_grant_slots(
    proxy_env: dict[str, Any],
) -> None:
    """Each request gives its grant slot back, so the count ends empty."""
    proxy_env["write"](["pypi.org"])
    proxy_env["proxy"].resolve = Resolver(["151.101.0.223"])
    for _ in range(ep.MAX_PER_GRANT + 3):
        proxy_env["upstream"] = Upstream(_echo_upstream)
        reply = _exchange(
            proxy_env["proxy"], b"CONNECT pypi.org:443 HTTP/1.1\r\n" + _auth() + b"\r\n",
        )
        assert reply.startswith(b"HTTP/1.1 200"), reply[:40]
    reasons = [json.loads(x)["reason"] for x in proxy_env["lines"]]
    assert "grant_busy" not in reasons and len(reasons) == ep.MAX_PER_GRANT + 3
    assert proxy_env["proxy"]._per_grant == {}
    assert proxy_env["proxy"]._preauth == {}


def test_the_internal_error_line_names_the_grant_when_one_was_found(
    proxy_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise ValueError("unexpected")

    monkeypatch.setattr(ep, "check_host_header", boom)
    proxy_env["write"](["pypi.org"])
    reply = _exchange(
        proxy_env["proxy"],
        b"GET http://pypi.org/ HTTP/1.1\r\nHost: pypi.org\r\n" + _auth() + b"\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 500")
    (line,) = proxy_env["lines"]
    parsed = json.loads(line)
    assert parsed["reason"] == "internal_error"
    assert (parsed["organization"], parsed["run"], parsed["approver"]) == (
        "org-a", "run-1", "member@example.com",
    )
    assert parsed["host"] == "pypi.org" and parsed["method"] == "GET"


# ── WS-43g2a: the door wait (§7.3, task 4070) ────────────────────────────────


class _Timed:
    """An upstream socket that records the timeout in force at each read."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self.read_timeouts: list[float | None] = []

    def recv(self, size: int) -> bytes:
        self.read_timeouts.append(self._sock.gettimeout())
        return self._sock.recv(size)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sock, name)


def _timed_proxy(proxy_env: dict[str, Any], **kwargs: Any) -> tuple[Any, list[_Timed]]:
    timed: list[_Timed] = []

    def connect(address: str, port: int) -> Any:
        proxy_env["connects"].append((address, port))
        timed.append(_Timed(proxy_env["upstream"].near))
        return timed[-1]

    proxy = ep.Proxy(
        str(proxy_env["grants"]), resolve=proxy_env["resolver"], connect=connect,
        clock=lambda: NOW, emit=proxy_env["lines"].append, **kwargs,
    )
    return proxy, timed


def test_the_door_read_waits_the_approval_wait_plus_30(proxy_env: dict[str, Any]) -> None:
    """Done-when 13: a held batch keeps the door request open for the card wait."""
    assert ep.DOOR_READ_TIMEOUT_S == 330.0, "the card wait of 300 plus 30"
    proxy_env["write"]([ep.DOOR_HOST], door="10.20.0.5:8000")
    proxy_env["upstream"] = Upstream(_http_upstream(b'{"ok":true}'))
    proxy, timed = _timed_proxy(proxy_env, door_read_timeout=630)
    reply = _exchange(
        proxy,
        b"GET http://metorite-api.sandbox/crm/leads HTTP/1.1\r\n"
        b"Host: metorite-api.sandbox\r\nX-Sandbox-Capability: msbx_abc\r\n" + _auth() + b"\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 200 OK")
    (upstream,) = timed
    assert upstream.read_timeouts and set(upstream.read_timeouts) == {630.0}
    assert ep.Proxy(str(proxy_env["grants"])).door_read_timeout == ep.DOOR_READ_TIMEOUT_S


def test_other_hosts_keep_the_60_second_read(proxy_env: dict[str, Any]) -> None:
    """Done-when 13: only the door waits longer. A plain forward reads 60 s."""
    assert ep.FORWARD_TIMEOUT_S == 60.0
    proxy_env["write"](["pypi.org"])
    proxy_env["upstream"] = Upstream(_http_upstream(b"ok"))
    proxy, timed = _timed_proxy(proxy_env, door_read_timeout=630)
    reply = _exchange(
        proxy, b"GET http://pypi.org/simple/ HTTP/1.1\r\nHost: pypi.org\r\n" + _auth() + b"\r\n",
    )
    assert reply.startswith(b"HTTP/1.1 200 OK")
    (upstream,) = timed
    assert upstream.read_timeouts and set(upstream.read_timeouts) == {ep.FORWARD_TIMEOUT_S}


@pytest.mark.parametrize("value", ["89", "89.9", "0", "-5", "nan"])
def test_a_door_read_timeout_under_90_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    """Done-when 13: a card wait below 60 s stops the proxy, so every grant
    fails closed. ``main`` returns 2 and serves nothing."""
    served: list[Any] = []
    monkeypatch.setattr(ep, "serve", lambda *a: served.append(a))
    printed: list[str] = []
    monkeypatch.setattr(ep, "_print_line", printed.append)
    grants = str(tmp_path / "grants.json")
    assert ep.main(["--grants", grants, "--door-read-timeout", value]) == 2
    assert served == []
    assert "refused_to_start" in printed[0]
    with pytest.raises(ValueError):
        ep.Proxy(grants, door_read_timeout=float(value))
    assert ep.main(["--grants", grants, "--door-read-timeout", "90"]) == 0
    assert served == [(grants, f"0.0.0.0:{ep.LISTEN_PORT}", 90.0)]
