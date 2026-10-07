"""Bulk Unsubscribe: real server-side unsubscribe + provider filters.

Covers the new behaviour that brings us to (and past) inbox-zero parity:
  • HTML-body unsubscribe-link scraping fallback (header preferred)
  • the RFC 8058 one-click engine (POST → GET fallback) + SSRF guard
  • the mailto: unsubscribe send path
  • Gmail auto-archive filter creation (skip-inbox for future mail)
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from email_ingestion.providers.base import (
    best_unsubscribe_link,
    find_unsubscribe_link_in_html,
)
from email_ingestion.providers.gmail import GmailProvider
from gateway import outbound_guard
from gateway.routes.email.automation import senders as s

from tests.unit._email_fakes import bind_db

# ── HTML-body scraping + best-link selection ────────────────────────────────

def test_scrape_finds_unsubscribe_anchor_and_decodes_entities() -> None:
    html = (
        '<a href="https://n.example/click?a=1">Read more</a>'
        '<a href="https://n.example/unsub?id=42&amp;u=9">Unsubscribe</a>'
    )
    assert find_unsubscribe_link_in_html(html) == "https://n.example/unsub?id=42&u=9"


def test_scrape_matches_on_anchor_text_not_just_href() -> None:
    html = '<a href="https://n.example/p/abc123">Manage your subscription</a>'
    assert find_unsubscribe_link_in_html(html) == "https://n.example/p/abc123"


def test_scrape_ignores_unrelated_and_non_http_links() -> None:
    assert find_unsubscribe_link_in_html('<a href="https://n.example/home">Home</a>') is None
    assert find_unsubscribe_link_in_html('<a href="mailto:u@x">Unsubscribe</a>') is None
    assert find_unsubscribe_link_in_html("") is None
    assert find_unsubscribe_link_in_html(None) is None


def test_best_link_prefers_header_over_body() -> None:
    html = '<a href="https://body/unsub">Unsubscribe</a>'
    assert best_unsubscribe_link("<https://hdr/u>, <mailto:u@x>", html) == "https://hdr/u"
    # No header → fall back to the scraped body link.
    assert best_unsubscribe_link("", html) == "https://body/unsub"
    # Header with only a mailto: still wins over the body (mailto is one-click-able).
    assert best_unsubscribe_link("<mailto:u@x>", html) == "mailto:u@x"


# ── SSRF guard ───────────────────────────────────────────────────────────────

def test_host_is_public_blocks_internal_targets() -> None:
    assert s._host_is_public("127.0.0.1") is False
    assert s._host_is_public("localhost") is False
    assert s._host_is_public("10.0.0.5") is False
    assert s._host_is_public("169.254.0.1") is False  # link-local
    assert s._host_is_public("8.8.8.8") is True


async def test_is_safe_external_url_rejects_bad_scheme_and_internal() -> None:
    assert await s._is_safe_external_url("https://8.8.8.8/u") is True
    assert await s._is_safe_external_url("file:///etc/passwd") is False
    assert await s._is_safe_external_url("http://127.0.0.1/x") is False
    assert await s._is_safe_external_url("not-a-url") is False


# ── One-click engine (POST → GET fallback) ──────────────────────────────────

class _Server:
    """A fake list server behind the outbound guard (EM-T13b-2).

    The guard resolves ``list.example`` to one public address and sends each
    request to it, so the fake sees the pinned request."""

    def __init__(self, post_status: int, get_status: int = 200) -> None:
        self._status = {"POST": post_status, "GET": get_status}
        self.calls: list[str] = []
        self.hosts: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.method)
        self.hosts.append(request.url.host)
        return httpx.Response(self._status[request.method],
                              stream=httpx.ByteStream(b""))


def _serve(monkeypatch: pytest.MonkeyPatch, server: _Server) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(outbound_guard, "_resolve",
                        lambda host, port: ["93.184.216.34"])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(server.handle), **kw))


async def test_http_unsubscribe_one_click_post_succeeds(
        monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(post_status=200)
    _serve(monkeypatch, server)
    ok, detail = await s._http_unsubscribe("https://list.example/u")
    assert ok is True and detail == "one-click-post"
    assert server.calls == ["POST"]  # no GET needed
    assert server.hosts == ["93.184.216.34"]  # the pinned address


async def test_http_unsubscribe_falls_back_to_get(
        monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(post_status=405, get_status=200)
    _serve(monkeypatch, server)
    ok, detail = await s._http_unsubscribe("https://list.example/u")
    assert ok is True and detail == "get"
    assert server.calls == ["POST", "GET"]


async def test_http_unsubscribe_rejects_unsafe_url_without_network(
        monkeypatch: pytest.MonkeyPatch) -> None:
    server = _Server(post_status=200)
    _serve(monkeypatch, server)
    monkeypatch.setattr(outbound_guard, "_resolve",
                        lambda host, port: ["127.0.0.1"])
    ok, detail = await s._http_unsubscribe("http://list.example/u")
    assert ok is False and detail == "unsafe-url"
    assert server.calls == []


# ── mailto: unsubscribe send ─────────────────────────────────────────────────

async def test_mailto_unsubscribe_sends_via_provider() -> None:
    prov = SimpleNamespace(send_message=AsyncMock(return_value="sent-1"))
    ok, detail = await s._mailto_unsubscribe(
        prov, "mailto:unsub@list.com?subject=Stop&body=please")
    assert ok is True and detail == "mailto"
    prov.send_message.assert_awaited_once()
    kwargs = prov.send_message.await_args.kwargs
    assert kwargs["to"] == ["unsub@list.com"]
    assert kwargs["subject"] == "Stop"


# ── Gmail auto-archive filter (future mail skips the inbox) ──────────────────

async def test_gmail_create_filter_skips_inbox() -> None:
    captured: dict[str, object] = {}

    class _GResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"id": "filter-99"}

    class _GClient:
        async def post(self, path: str, json: dict[str, object]) -> _GResp:
            captured["path"] = path
            captured["json"] = json
            return _GResp()

    prov = GmailProvider({"access_token": "x"})
    with patch.object(prov, "_get_client", AsyncMock(return_value=_GClient())):
        fid = await prov.create_filter(from_email="news@x.com", archive=True)
    assert fid == "filter-99"
    assert captured["path"] == "/users/me/settings/filters"
    assert captured["json"]["criteria"] == {"from": "news@x.com"}
    assert captured["json"]["action"]["removeLabelIds"] == ["INBOX"]


async def test_gmail_delete_filter_tolerates_missing() -> None:
    seen: dict[str, str] = {}

    class _DResp:
        status_code = 404  # already gone — must not raise

        def raise_for_status(self) -> None:  # pragma: no cover - shouldn't run
            raise AssertionError("404 should be tolerated")

    class _DClient:
        async def delete(self, path: str) -> _DResp:
            seen["path"] = path
            return _DResp()

    prov = GmailProvider({"access_token": "x"})
    with patch.object(prov, "_get_client", AsyncMock(return_value=_DClient())):
        await prov.delete_filter("filter-99")
    assert seen["path"] == "/users/me/settings/filters/filter-99"


# ── Endpoint decision logic: unsubscribed vs blocked ────────────────────────

USER = SimpleNamespace(email="u@example.com")


async def test_unsubscribe_endpoint_marks_unsubscribed_on_success() -> None:
    captured: dict[str, object] = {}

    async def _apply(db, bg, aid, email, name, status, link, *, create_filter):
        captured.update(status=status, create_filter=create_filter)
        return 4

    req = s.UnsubscribeRequest(
        account_id="acc-1", email="news@x.com",
        unsubscribe_link="https://list.example/u")
    with patch.object(s, "_tenant_session", bind_db(AsyncMock())), \
            patch.object(s, "_assert_account_owner", AsyncMock()), \
            patch.object(s, "_http_unsubscribe",
                         AsyncMock(return_value=(True, "one-click-post"))), \
            patch.object(s, "_apply_newsletter_status", _apply):
        res = await s.unsubscribe_sender(req, SimpleNamespace(add_task=lambda *a: None),
                                         user=USER)
    assert res["ok"] is True
    assert res["method"] == "one-click"
    assert res["status"] == "UNSUBSCRIBED"
    assert captured == {"status": "UNSUBSCRIBED", "create_filter": False}


async def test_unsubscribe_endpoint_blocks_when_no_link() -> None:
    captured: dict[str, object] = {}

    async def _apply(db, bg, aid, email, name, status, link, *, create_filter):
        captured.update(status=status, create_filter=create_filter)
        return 0

    # DB lookup for a stored link returns none.
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        fetchone=lambda: SimpleNamespace(link=None))
    req = s.UnsubscribeRequest(account_id="acc-1", email="news@x.com")
    with patch.object(s, "_tenant_session", bind_db(db)), \
            patch.object(s, "_assert_account_owner", AsyncMock()), \
            patch.object(s, "_apply_newsletter_status", _apply):
        res = await s.unsubscribe_sender(req, SimpleNamespace(add_task=lambda *a: None),
                                         user=USER)
    assert res["ok"] is False
    assert res["method"] == "blocked"
    assert res["status"] == "AUTO_ARCHIVED"
    # Blocking must create a provider filter for future mail.
    assert captured == {"status": "AUTO_ARCHIVED", "create_filter": True}
