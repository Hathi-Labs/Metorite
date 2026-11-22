"""Gmail gateway faults (HRD-20, task 3224).

R7 fences named here:

* ``gmail-get-no-retry``: a GET that gets a 5xx goes back to the caller.
* ``gmail-write-no-retry``: a send or a draft create is sent once. A 502 or a
  read timeout raises ``ProviderDidNotAnswer`` with "Gmail did not answer".
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from email_ingestion.providers import base as base_mod
from email_ingestion.providers.base import ProviderDidNotAnswer, guard_client
from email_ingestion.providers.gmail import GMAIL_API_BASE, _write_kind


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(base_mod.asyncio, "sleep", fake_sleep)
    return waits


def _client(statuses, calls, raises=None):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if raises is not None:
            raise raises
        return httpx.Response(
            statuses[min(len(calls) - 1, len(statuses) - 1)], json={},
            request=request)

    return guard_client(httpx.AsyncClient(
        base_url=GMAIL_API_BASE, transport=httpx.MockTransport(handler)),
        "Gmail", _write_kind, retry_reads=False)


def test_get_502_is_not_retried_for_gmail():
    """``test_gmail_rate_limits.py`` fences "a 5xx is not retried", so the
    Gmail guard leaves reads alone (the Outlook guard retries them)."""
    calls: list[httpx.Request] = []
    resp = asyncio.run(_client([502, 200], calls).get("/users/me/labels"))
    assert resp.status_code == 502
    assert len(calls) == 1


@pytest.mark.parametrize("path,kind", [
    ("/users/me/messages/send", "send"),
    ("/users/me/drafts/send", "send"),
    ("/users/me/drafts", "draft"),
])
def test_write_502_is_one_call_and_clear_text(path, kind):
    calls: list[httpx.Request] = []
    with pytest.raises(ProviderDidNotAnswer) as err:
        asyncio.run(_client([502, 200], calls).post(path, json={}))
    assert len(calls) == 1
    assert err.value.detail.startswith("Gmail did not answer. It may not have ")


def test_write_read_timeout_gives_the_same_error():
    calls: list[httpx.Request] = []
    with pytest.raises(ProviderDidNotAnswer):
        asyncio.run(_client([200], calls, raises=httpx.ReadTimeout("x")).post(
            "/users/me/messages/send", json={}))
    assert len(calls) == 1


def test_other_posts_keep_their_old_path():
    calls: list[httpx.Request] = []
    resp = asyncio.run(_client([502], calls).post(
        "/users/me/messages/abc/modify", json={}))
    assert resp.status_code == 502
