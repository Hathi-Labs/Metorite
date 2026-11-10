"""WS-8c A6 — the MCP client of ``gateway/outbound_guard.py`` is pinned.

Spec: ``project-docs/specs/agent_architecture.md`` §12.2 WS-8c, acceptance A6.

``outbound_guard.mcp_client`` is the only client a native MAF agent's MCP tool
gets. These fences prove its rules without a network:

* A bad URL is refused BEFORE a socket opens: loopback, link-local metadata,
  RFC 1918, a public name that resolves to a private address, ``http`` and a
  port that is not 443. The scheme and the port are refused before any DNS
  lookup.
* Each request goes to the address that the check resolved, with the name in
  ``Host`` and in ``sni_hostname``. A second request does not resolve again.
* A 3xx answer is a refusal, and nothing follows it.
* A request to another origin is refused, and no socket opens for it.
* ``follow_redirects`` and ``trust_env`` are both off.

The inner transport is :func:`outbound_guard._mcp_inner_transport`. Each test
replaces it with a recorder, so "no socket" means "the recorder saw nothing".
"""
from __future__ import annotations

import httpx
import pytest
from gateway import outbound_guard

PUBLIC_IP = "93.184.216.34"


class _Inner:
    """Stands for the socket. Records each request and answers ``status``."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []
        self.built = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Inner:
        def _factory() -> httpx.AsyncBaseTransport:
            self.built += 1
            return httpx.MockTransport(self._handle)

        monkeypatch.setattr(outbound_guard, "_mcp_inner_transport", _factory)
        return self

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        headers = {"location": "https://evil.example.com/"} if 300 <= self.status < 400 else {}
        return httpx.Response(self.status, headers=headers, content=b"{}")


class _Dns:
    """Replaces ``_resolve``. Counts each lookup."""

    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = table
        self.calls: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Dns:
        def _resolve(host: str, port: int) -> list[str]:
            self.calls.append(host)
            return list(self.table.get(host, [host.strip("[]")]))

        monkeypatch.setattr(outbound_guard, "_resolve", _resolve)
        return self


REFUSED = [
    pytest.param("https://127.0.0.1/mcp", {}, "not public", id="loopback"),
    pytest.param("https://169.254.169.254/mcp", {}, "not public", id="metadata"),
    pytest.param("https://10.1.2.3/mcp", {}, "not public", id="rfc1918-10"),
    pytest.param("https://192.168.0.10/mcp", {}, "not public", id="rfc1918-192"),
    pytest.param("https://mcp.example.com/mcp", {"mcp.example.com": ["172.16.0.5"]},
                 "not public", id="public-name-to-rfc1918"),
    pytest.param("http://mcp.example.com/mcp", {"mcp.example.com": [PUBLIC_IP]},
                 "scheme", id="http"),
    pytest.param("https://mcp.example.com:8443/mcp", {"mcp.example.com": [PUBLIC_IP]},
                 "port", id="port-8443"),
    pytest.param("https://mcp.example.com:80/mcp", {"mcp.example.com": [PUBLIC_IP]},
                 "port", id="port-80"),
]


@pytest.mark.parametrize(("url", "dns", "reason"), REFUSED)
async def test_a_bad_url_is_refused_before_a_socket_opens(url, dns, reason, monkeypatch) -> None:
    inner = _Inner().install(monkeypatch)
    lookups = _Dns(dns).install(monkeypatch)
    with pytest.raises(outbound_guard.OutboundRefused) as caught:
        await outbound_guard.mcp_client(url)
    assert caught.value.reason == reason
    assert inner.built == 0 and inner.requests == [], "a socket was prepared for a refused URL"
    if reason in ("scheme", "port"):
        assert lookups.calls == [], "the scheme and port refusal must come before DNS"


def _public(monkeypatch: pytest.MonkeyPatch) -> _Dns:
    monkeypatch.setattr(outbound_guard, "_is_local_address", lambda _a: False)
    return _Dns({"mcp.example.com": [PUBLIC_IP]}).install(monkeypatch)


async def test_each_request_goes_to_the_checked_address(monkeypatch) -> None:
    inner = _Inner().install(monkeypatch)
    lookups = _public(monkeypatch)
    client = await outbound_guard.mcp_client("https://mcp.example.com/mcp")
    try:
        await client.post("https://mcp.example.com/mcp", content=b"{}")
        await client.get("https://mcp.example.com/mcp")
    finally:
        await client.aclose()
    assert len(inner.requests) == 2
    for sent in inner.requests:
        assert sent.url.host == PUBLIC_IP
        assert sent.headers["host"] == "mcp.example.com"
        assert sent.extensions.get("sni_hostname") == "mcp.example.com"
    assert lookups.calls == ["mcp.example.com"], "a second request resolved the name again"


async def test_a_redirect_is_refused_and_not_followed(monkeypatch) -> None:
    inner = _Inner(status=307).install(monkeypatch)
    _public(monkeypatch)
    client = await outbound_guard.mcp_client("https://mcp.example.com/mcp")
    try:
        with pytest.raises(outbound_guard.OutboundRefused) as caught:
            await client.post("https://mcp.example.com/mcp", content=b"{}")
    finally:
        await client.aclose()
    assert caught.value.reason == "redirect 307"
    assert len(inner.requests) == 1, "the client followed the redirect"


async def test_another_origin_is_refused_with_no_request(monkeypatch) -> None:
    inner = _Inner().install(monkeypatch)
    _public(monkeypatch)
    client = await outbound_guard.mcp_client("https://mcp.example.com/mcp")
    try:
        for other in ("https://evil.example.com/mcp", "http://mcp.example.com/mcp",
                      "https://mcp.example.com:8443/mcp"):
            with pytest.raises(outbound_guard.OutboundRefused) as caught:
                await client.get(other)
            assert caught.value.reason == "other origin"
    finally:
        await client.aclose()
    assert inner.requests == []


async def test_the_client_follows_no_redirect_and_reads_no_proxy_env(monkeypatch) -> None:
    _Inner().install(monkeypatch)
    _public(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    client = await outbound_guard.mcp_client("https://mcp.example.com/mcp")
    try:
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert isinstance(client._transport, outbound_guard._PinnedTransport)
        assert client._mounts == {}, "a proxy mount came from the environment"
    finally:
        await client.aclose()


def test_a_refusal_names_no_path_and_no_query() -> None:
    exc = outbound_guard.OutboundRefused("mcp.example.com", "redirect 302")
    assert str(exc) == "mcp.example.com redirect 302"


async def test_the_session_delete_gets_a_short_timeout(monkeypatch) -> None:
    """Review round 2 of #869: the run waits on the session DELETE in its
    ``finally``, so the 300 s read cap must not apply to it."""
    inner = _Inner().install(monkeypatch)
    _public(monkeypatch)
    client = await outbound_guard.mcp_client("https://mcp.example.com/mcp")
    try:
        await client.delete("https://mcp.example.com/mcp")
        await client.post("https://mcp.example.com/mcp", content=b"{}")
    finally:
        await client.aclose()
    delete, post = inner.requests
    cap = outbound_guard.MCP_DELETE_TIMEOUT_S
    assert delete.extensions["timeout"]["read"] == cap <= 5
    assert post.extensions["timeout"]["read"] == 300.0


# ── POST /mcp checks the URL before it writes a row (review round 2, P0) ────


REGISTER_REFUSED = [
    pytest.param("https://169.254.169.254/latest/meta-data", {}, id="metadata"),
    pytest.param("https://127.0.0.1/mcp", {}, id="loopback"),
    pytest.param("https://10.0.0.8/mcp", {}, id="rfc1918"),
    pytest.param("https://mcp.example.com/mcp", {"mcp.example.com": ["192.168.4.4"]},
                 id="name-to-rfc1918"),
    pytest.param("http://mcp.example.com/mcp", {"mcp.example.com": [PUBLIC_IP]}, id="http"),
    pytest.param("https://mcp.example.com:8443/mcp", {"mcp.example.com": [PUBLIC_IP]},
                 id="port"),
]


@pytest.mark.parametrize(("url", "dns"), REGISTER_REFUSED)
async def test_post_mcp_refuses_an_internal_url_and_writes_nothing(url, dns, monkeypatch):
    from acb_auth import UserContext, UserRole
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import HTTPException
    from gateway.routes import integrations

    from tests.unit._maf_mcp_fakes import refuse_registry

    _Dns(dns).install(monkeypatch)
    # IN-4: the registry reader is the only way to the store.
    refuse_registry(monkeypatch, "a refused URL reached the database")
    org = "dddddddd-0000-0000-0000-00000000000d"
    user = UserContext(email="admin@example.test", role=UserRole.EXECUTIVE, organization_id=org)
    req = integrations.McpServerRequest(
        name="probe", transport="http-sse", url=url, headers={"X-Internal": "1"},
        agent_scope=["*"],
    )
    clear_tenant()
    token = bind_tenant(org)
    try:
        with pytest.raises(HTTPException) as caught:
            await integrations.register_mcp_server(req, user=user)
    finally:
        release_tenant(token)
        clear_tenant()
    assert caught.value.status_code == 422
    assert "does not connect" in caught.value.detail
