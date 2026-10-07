"""The outbound guard of a webhook and of an unsubscribe link (EM-T13b-2).

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.15, the subsection
EM-T13b. ``gateway/outbound_guard.py`` is the one seam. The rule action
``CALL_WEBHOOK`` (``routes/email/automation/actions.py``) and the one-click
unsubscribe (``senders._http_unsubscribe``) both call it.

The fences, in the order of the spec:

- A private, loopback, link-local, shared, mapped or multicast address is
  refused, and no request goes out.
- A host name that resolves to a private address is refused. So is a host with
  one private address among public ones.
- The request goes to the resolved address with the ``Host`` header and the
  SNI name of the URL, and the guard resolves the host once. A real local TLS
  server proves that the certificate check uses the name.
- The guard does not follow a 3xx.
- The guard reads no more of the answer than the cap.
- The guard refuses ``file:``, ``ftp:``, a user name and a backslash.
- A refusal writes the host and the reason to ``errors_out``, with no path and
  no query.

Every test replaces the network with a recorder, so no test reaches the
internet. The TLS test listens on 127.0.0.1 only.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import ssl
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from gateway import outbound_guard as g
from gateway.routes.email.automation import actions

PUBLIC_IP = "93.184.216.34"
SECOND_PUBLIC_IP = "93.184.216.35"


# ── The recorders ────────────────────────────────────────────────────────────


class _Net:
    """Stands in for the network under each ``httpx.AsyncClient``.

    It records each request that leaves and the arguments of each client."""

    def __init__(self, answer: Callable[[httpx.Request], Any] | None = None):
        self.requests: list[httpx.Request] = []
        self.clients: list[dict[str, Any]] = []
        self._answer = answer or (lambda _r: httpx.Response(
            200, stream=httpx.ByteStream(b"ok")))

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self._answer(request)
        if asyncio.iscoroutine(answer):
            answer = await answer
        return answer

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Net:
        real = httpx.AsyncClient

        def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            self.clients.append(kwargs)
            return real(*args, transport=httpx.MockTransport(self._handle),
                        **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", _client)
        return self


class _Resolver:
    """Stands in for DNS, and counts each lookup."""

    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = table
        self.calls: list[str] = []

    def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        if host not in self.table:
            raise OSError("no such host")
        return list(self.table[host])

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Resolver:
        monkeypatch.setattr(g, "_resolve", self)
        return self


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> _Net:
    return _Net().install(monkeypatch)


async def _webhook(url: str) -> tuple[list[str], list[dict[str, str]]]:
    """Run one ``CALL_WEBHOOK`` action through ``_apply_rule_actions``."""
    errors: list[dict[str, str]] = []
    done = await actions._apply_rule_actions(
        AsyncMock(), None, "msg-1", "pm-1",
        [{"type": "CALL_WEBHOOK", "url": url}], errors_out=errors)
    return done, errors


# ── The check: an address that is not public ─────────────────────────────────

_NOT_PUBLIC_LITERALS = [
    "http://127.0.0.1/hook",
    "http://10.0.0.5/hook",
    "http://169.254.169.254/latest/meta-data/",
    "http://100.64.0.1/hook",
    "http://[::1]/hook",
    "http://[::ffff:127.0.0.1]/hook",
    "http://224.0.0.1/hook",
    "https://0.0.0.0/hook",
    "http://[fe80::1]/hook",
]


@pytest.mark.parametrize("url", _NOT_PUBLIC_LITERALS)
async def test_the_guard_refuses_an_address_that_is_not_public(
        net: _Net, url: str) -> None:
    with pytest.raises(g.OutboundRefused) as err:
        await g.request("POST", url, json={"message_id": "m"})
    assert err.value.reason == "not public"
    assert net.requests == []


@pytest.mark.parametrize("url", _NOT_PUBLIC_LITERALS)
async def test_a_webhook_to_an_address_that_is_not_public_sends_nothing(
        net: _Net, url: str) -> None:
    done, errors = await _webhook(url)
    assert done == []
    assert net.requests == []
    assert len(errors) == 1
    assert errors[0]["type"] == "CALL_WEBHOOK"
    assert errors[0]["error"].startswith("webhook refused: ")
    assert errors[0]["error"].endswith(" not public")


def _glibc_like(host: str, port: int) -> list[str]:
    """A resolver that reads the old numeric forms as glibc does.

    Windows refuses ``2130706433`` and ``0177.0.0.1``, and Linux answers
    127.0.0.1. The guard must refuse both answers."""
    table = {
        "2130706433": ["127.0.0.1"],
        "0177.0.0.1": ["127.0.0.1"],
        "0x7f.1": ["127.0.0.1"],
        "127.1": ["127.0.0.1"],
        "localhost": ["127.0.0.1", "::1"],
    }
    return table[host]


@pytest.mark.parametrize(("url", "reason"), [
    ("http://2130706433/hook", "not public"),
    # httpx refuses a dotted form with a leading zero before any lookup.
    ("http://0177.0.0.1/hook", "bad url"),
    ("http://0x7f.1/hook", "not public"),
    ("http://127.1/hook", "not public"),
    ("http://localhost/hook", "not public"),
    ("http://localhost:8000/hook", "not public"),
])
async def test_a_numeric_form_or_localhost_is_refused(
        net: _Net, monkeypatch: pytest.MonkeyPatch, url: str,
        reason: str) -> None:
    monkeypatch.setattr(g, "_resolve", _glibc_like)
    with pytest.raises(g.OutboundRefused) as err:
        await g.request("POST", url)
    assert err.value.reason == reason
    assert net.requests == []


@pytest.mark.parametrize("url", [
    "http://0177.0.0.1/hook",
    "http://localhost/hook",
])
async def test_the_real_resolver_refuses_a_numeric_form_or_localhost(
        net: _Net, url: str) -> None:
    """With the resolver of this platform, the answer is a refusal too.

    Neither case asks a DNS server. ``2130706433`` is not here, because
    Windows sends it to DNS. The glibc-like fake above covers it."""
    with pytest.raises(g.OutboundRefused) as err:
        await g.request("POST", url)
    assert err.value.reason in ("not public", "unresolvable", "bad url")
    assert net.requests == []


async def test_a_host_name_that_resolves_to_a_private_address_is_refused(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    _Resolver({"hook.example": ["10.0.0.5"]}).install(monkeypatch)
    with pytest.raises(g.OutboundRefused) as err:
        await g.request("POST", "https://hook.example/in")
    assert (err.value.host, err.value.reason) == ("hook.example", "not public")
    assert net.requests == []


async def test_one_private_address_among_public_ones_is_refused(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    _Resolver({"hook.example": [PUBLIC_IP, "10.0.0.5"]}).install(monkeypatch)
    with pytest.raises(g.OutboundRefused):
        await g.request("POST", "https://hook.example/in")
    assert net.requests == []


async def test_a_mapped_private_address_from_dns_is_refused(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    _Resolver({"hook.example": ["::ffff:10.0.0.5"]}).install(monkeypatch)
    with pytest.raises(g.OutboundRefused):
        await g.request("POST", "https://hook.example/in")
    assert net.requests == []


async def test_a_host_that_does_not_resolve_is_refused(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    _Resolver({}).install(monkeypatch)
    with pytest.raises(g.OutboundRefused) as err:
        await g.request("POST", "https://nowhere.example/in")
    assert err.value.reason == "unresolvable"
    assert net.requests == []


# ── The check: the form of the URL ───────────────────────────────────────────


@pytest.mark.parametrize(("url", "reason"), [
    ("file:///etc/passwd", "scheme"),
    ("ftp://hook.example/in", "scheme"),
    ("gopher://hook.example/in", "scheme"),
    ("mailto:a@hook.example", "scheme"),
    ("not-a-url", "scheme"),
    ("http://user@hook.example/in", "user name"),
    ("http://user:pw@hook.example/in", "user name"),
    ("http://:pw@hook.example/in", "user name"),
    ("http://hook.example\\@10.0.0.5/in", "backslash"),
    ("http:///in", "no host"),
])
async def test_the_guard_refuses_the_form_before_any_lookup(
        net: _Net, monkeypatch: pytest.MonkeyPatch, url: str,
        reason: str) -> None:
    dns = _Resolver({"hook.example": [PUBLIC_IP]}).install(monkeypatch)
    with pytest.raises(g.OutboundRefused) as err:
        await g.request("POST", url)
    assert err.value.reason == reason
    assert dns.calls == []
    assert net.requests == []


async def test_the_old_names_answer_through_the_one_seam(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """``senders`` imports the two old names again (item 1)."""
    from gateway.routes.email.automation import senders

    assert senders._host_is_public is g._host_is_public
    assert senders._is_safe_external_url is g._is_safe_external_url
    _Resolver({"hook.example": ["100.64.0.1"]}).install(monkeypatch)
    assert g._host_is_public("hook.example") is False
    assert await g._is_safe_external_url("https://hook.example/") is False


# ── The pin ──────────────────────────────────────────────────────────────────


async def test_the_request_goes_to_the_resolved_address_with_the_name(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    dns = _Resolver(
        {"hook.example": [PUBLIC_IP, SECOND_PUBLIC_IP]}).install(monkeypatch)
    answer = await g.request(
        "POST", "https://hook.example/in/path?x=1", json={"message_id": "m"})
    assert answer.status_code == 200
    assert dns.calls == ["hook.example"]  # one lookup, no second answer
    [req] = net.requests
    assert req.url.host == PUBLIC_IP  # the first address, not the name
    assert req.url.scheme == "https"
    assert req.url.raw_path == b"/in/path?x=1"
    assert req.headers["host"] == "hook.example"
    assert req.extensions["sni_hostname"] == "hook.example"


async def test_a_port_stays_in_the_host_header(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    _Resolver({"hook.example": [PUBLIC_IP]}).install(monkeypatch)
    await g.request("GET", "https://hook.example:8443/in")
    [req] = net.requests
    assert (req.url.host, req.url.port) == (PUBLIC_IP, 8443)
    assert req.headers["host"] == "hook.example:8443"
    assert req.extensions["sni_hostname"] == "hook.example"


async def test_an_ipv6_address_is_pinned_in_brackets(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    _Resolver({"hook.example": ["2606:4700::1111"]}).install(monkeypatch)
    await g.request("GET", "https://hook.example/in")
    [req] = net.requests
    assert req.url.host == "2606:4700::1111"
    assert req.headers["host"] == "hook.example"


async def test_plain_http_sends_the_host_header_and_no_sni(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    _Resolver({"hook.example": [PUBLIC_IP]}).install(monkeypatch)
    await g.request("GET", "http://hook.example/in")
    [req] = net.requests
    assert req.url.host == PUBLIC_IP
    assert req.headers["host"] == "hook.example"
    assert "sni_hostname" not in req.extensions


async def test_the_client_reads_no_proxy_and_follows_no_redirect(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    _Resolver({"hook.example": [PUBLIC_IP]}).install(monkeypatch)
    await g.request("GET", "https://hook.example/in")
    [kwargs] = net.clients
    assert kwargs["trust_env"] is False
    assert kwargs["follow_redirects"] is False
    timeout = kwargs["timeout"]
    assert timeout.connect == 3.0
    assert timeout.read == 10.0
    assert (g.CONNECT_TIMEOUT_S, g.TOTAL_TIMEOUT_S) == (3.0, 10.0)


async def test_a_webhook_posts_the_same_body_to_the_pinned_address(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    dns = _Resolver({"hook.example": [PUBLIC_IP]}).install(monkeypatch)
    done, errors = await _webhook("https://hook.example/in?k=v")
    assert (done, errors) == (["CALL_WEBHOOK"], [])
    assert dns.calls == ["hook.example"]
    [req] = net.requests
    assert req.method == "POST"
    assert req.url.host == PUBLIC_IP
    assert req.headers["host"] == "hook.example"
    assert req.read() == b'{"message_id":"msg-1"}'


async def test_an_unsubscribe_link_goes_through_the_same_pin(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.routes.email.automation import senders

    dns = _Resolver({"list.example": [PUBLIC_IP]}).install(monkeypatch)
    ok, detail = await senders._http_unsubscribe("https://list.example/u?id=9")
    assert (ok, detail) == (True, "one-click-post")
    assert dns.calls == ["list.example"]
    [req] = net.requests
    assert req.url.host == PUBLIC_IP
    assert req.headers["host"] == "list.example"
    assert req.extensions["sni_hostname"] == "list.example"


# ── The pin, on a real TLS handshake ─────────────────────────────────────────


def _self_signed(name: str, folder: Path) -> tuple[Path, Path]:
    """A certificate for ``name`` and its key, written to ``folder``."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]),
                       critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                       critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = folder / f"{name}.pem"
    key_path = folder / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    return cert_path, key_path


class _TlsServer:
    """An HTTPS server on 127.0.0.1 that records the SNI name and the Host."""

    def __init__(self, cert: Path, key: Path) -> None:
        self.ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.ctx.load_cert_chain(cert, key)
        self.sni: list[str | None] = []
        self.hosts: list[str] = []
        self.ctx.sni_callback = lambda _s, name, _c: self.sni.append(name)
        self.port = 0

    async def _serve(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            for line in head.decode("latin-1").split("\r\n"):
                if line.lower().startswith("host:"):
                    self.hosts.append(line.split(":", 1)[1].strip())
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                         b"Connection: close\r\n\r\nok")
            await writer.drain()
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def __aenter__(self) -> _TlsServer:
        self._server = await asyncio.start_server(
            self._serve, "127.0.0.1", 0, ssl=self.ctx)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_: object) -> None:
        self._server.close()
        await self._server.wait_closed()


def _trusting(monkeypatch: pytest.MonkeyPatch, cert: Path) -> None:
    """Make each client of the guard trust ``cert``, and nothing else change."""
    real = httpx.AsyncClient
    ctx = ssl.create_default_context(cafile=str(cert))

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real(*args, verify=ctx, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)


async def test_tls_checks_the_certificate_against_the_name_not_the_address(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A real handshake to 127.0.0.1 passes only because SNI names the host.

    The guard refuses 127.0.0.1 in its check, so the test builds the pinned
    ``Target`` by hand and calls ``send``, the half that connects."""
    cert, key = _self_signed("hook.test", tmp_path)
    _trusting(monkeypatch, cert)
    async with _TlsServer(cert, key) as server:
        target = g.Target(
            url=httpx.URL(f"https://hook.test:{server.port}/in?x=1"),
            host="hook.test", ip="127.0.0.1")
        answer = await g.send(target, "POST", json={"message_id": "m"})
    assert (answer.status_code, answer.body) == (200, b"ok")
    assert server.sni == ["hook.test"]
    assert server.hosts == [f"hook.test:{server.port}"]


async def test_tls_refuses_a_certificate_for_another_name(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The same address with a certificate for another name fails closed."""
    cert, key = _self_signed("other.test", tmp_path)
    _trusting(monkeypatch, cert)
    async with _TlsServer(cert, key) as server:
        target = g.Target(
            url=httpx.URL(f"https://hook.test:{server.port}/in"),
            host="hook.test", ip="127.0.0.1")
        with pytest.raises(httpx.ConnectError):
            await g.send(target, "GET")


# ── No redirect ──────────────────────────────────────────────────────────────


def _redirect(_r: httpx.Request) -> httpx.Response:
    return httpx.Response(
        302, headers={"Location": "http://169.254.169.254/latest/meta-data/"},
        stream=httpx.ByteStream(b""))


async def test_the_guard_does_not_follow_a_3xx(
        monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net(_redirect).install(monkeypatch)
    _Resolver({"hook.example": [PUBLIC_IP]}).install(monkeypatch)
    with pytest.raises(g.OutboundRefused) as err:
        await g.request("POST", "https://hook.example/in")
    assert err.value.reason == "redirect 302"
    assert [r.url.host for r in net.requests] == [PUBLIC_IP]


async def test_a_webhook_3xx_is_recorded_as_a_failure(
        monkeypatch: pytest.MonkeyPatch) -> None:
    net = _Net(_redirect).install(monkeypatch)
    _Resolver({"hook.example": [PUBLIC_IP]}).install(monkeypatch)
    done, errors = await _webhook("https://hook.example/in")
    assert done == []
    assert errors == [{"type": "CALL_WEBHOOK",
                       "error": "webhook refused: hook.example redirect 302"}]
    assert len(net.requests) == 1


# ── The caps ─────────────────────────────────────────────────────────────────


class _BigStream(httpx.AsyncByteStream):
    """One MiB in chunks of 16 KiB, which counts each byte that it yields."""

    CHUNK = 16 * 1024

    def __init__(self) -> None:
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(64):
            self.yielded += self.CHUNK
            yield b"x" * self.CHUNK


async def test_the_guard_reads_no_more_of_the_answer_than_the_cap(
        monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _BigStream()
    _Net(lambda _r: httpx.Response(200, stream=stream)).install(monkeypatch)
    _Resolver({"hook.example": [PUBLIC_IP]}).install(monkeypatch)
    answer = await g.request("POST", "https://hook.example/in")
    assert len(answer.body) == g.MAX_ANSWER_BYTES == 64 * 1024
    assert stream.yielded <= g.MAX_ANSWER_BYTES


async def test_the_guard_stops_at_the_total_time(
        monkeypatch: pytest.MonkeyPatch) -> None:
    async def _slow(_r: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, stream=httpx.ByteStream(b""))

    _Net(_slow).install(monkeypatch)
    _Resolver({"hook.example": [PUBLIC_IP]}).install(monkeypatch)
    monkeypatch.setattr(g, "TOTAL_TIMEOUT_S", 0.05)
    with pytest.raises(g.OutboundRefused) as err:
        await g.request("POST", "https://hook.example/in")
    assert (err.value.host, err.value.reason) == ("hook.example", "timeout")


# ── The record ───────────────────────────────────────────────────────────────


async def test_a_refusal_writes_the_host_and_the_reason_and_no_path(
        net: _Net, monkeypatch: pytest.MonkeyPatch) -> None:
    _Resolver({"hook.example": ["10.0.0.5"]}).install(monkeypatch)
    log = MagicMock()
    monkeypatch.setattr(actions, "_log", log)
    done, errors = await _webhook(
        "https://hook.example/secret-path/run?token=abc123")
    assert done == []
    assert errors == [{"type": "CALL_WEBHOOK",
                       "error": "webhook refused: hook.example not public"}]
    log.warning.assert_called_once_with(
        "email.rule_action_failed", action="CALL_WEBHOOK",
        error="webhook refused: hook.example not public")
    for text in (str(errors), str(log.warning.call_args)):
        assert "secret-path" not in text
        assert "token" not in text
        assert "abc123" not in text
    assert net.requests == []


async def test_a_refused_form_names_no_path_either(net: _Net) -> None:
    done, errors = await _webhook("ftp://hook.example/secret-path?token=abc")
    assert done == []
    assert errors == [{"type": "CALL_WEBHOOK",
                       "error": "webhook refused: hook.example scheme"}]


def test_the_address_rule_reads_is_global_and_multicast() -> None:
    """The rule in one place: multicast, or not ``is_global``, is refused."""
    assert g._address_refusal([PUBLIC_IP]) is None
    assert g._address_refusal(["2606:4700::1111"]) is None
    assert g._address_refusal([]) == "unresolvable"
    for addr in ("100.64.0.1", "224.0.0.1", "::ffff:10.0.0.5", "ff02::1",
                 "192.0.2.1", "198.18.0.1", "::"):
        assert g._address_refusal([addr]) == "not public", addr
    # The shared range 100.64.0.0/10 is not private, so only is_global sees it.
    assert ipaddress.ip_address("100.64.0.1").is_private is False
