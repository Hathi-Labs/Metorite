"""WS-17 task 894: a Graph 412 on a write is read again and tried again.

A 412 (``ErrorIrresolvableConflict``) means the message changed between our
read and our write. ``OutlookProvider`` retries at most twice, with a short
wait, and only for a 412. ``set_labels`` reads the categories again each time.

Fence: ``_retry_on_412`` in ``providers/outlook.py``. Hermetic: one
``httpx.MockTransport``, and the sleep is patched.
"""
from __future__ import annotations

import httpx
import pytest
from email_ingestion.providers import outlook as mod
from email_ingestion.providers.outlook import OutlookProvider


class _Graph:
    def __init__(self, patch_codes: list[int], gets: list[list[str]]) -> None:
        self.patch_codes = list(patch_codes)
        self.gets = list(gets)
        self.calls: list[tuple[str, dict | None]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        import json
        if request.method == "GET":
            self.calls.append(("GET", None))
            cats = self.gets.pop(0) if len(self.gets) > 1 else self.gets[0]
            return httpx.Response(200, json={"categories": cats})
        body = json.loads(request.content)
        self.calls.append(("PATCH", body))
        return httpx.Response(self.patch_codes.pop(0), json={})


def _provider(graph: _Graph) -> OutlookProvider:
    p = OutlookProvider({"access_token": "x", "refresh_token": "y"})
    p._http = httpx.AsyncClient(
        base_url="https://graph.test/v1.0",
        transport=httpx.MockTransport(graph.handle))
    p._master_categories = {"news"}
    return p


@pytest.fixture()
def sleeps(monkeypatch):
    waited: list[float] = []

    async def fake(secs: float) -> None:
        waited.append(secs)

    monkeypatch.setattr(mod.asyncio, "sleep", fake)
    return waited


async def test_412_then_200_reads_again_and_keeps_the_new_category(sleeps):
    g = _Graph([412, 200], [["A"], ["A", "Appeared"]])
    await _provider(g).set_labels("m1", add=["news"])
    assert [c[0] for c in g.calls] == ["GET", "PATCH", "GET", "PATCH"]
    assert g.calls[3][1] == {"categories": ["A", "Appeared", "news"]}
    assert sleeps == [0.5]


async def test_three_412s_raise_after_two_retries(sleeps):
    g = _Graph([412, 412, 412], [["A"]])
    with pytest.raises(httpx.HTTPStatusError):
        await _provider(g).set_labels("m1", add=["news"])
    assert [c[0] for c in g.calls].count("PATCH") == 3
    assert sleeps == [0.5, 1.5]


@pytest.mark.parametrize("code", [404, 500])
async def test_other_statuses_do_not_retry(sleeps, code):
    g = _Graph([code], [["A"]])
    with pytest.raises(httpx.HTTPStatusError):
        await _provider(g).set_labels("m1", add=["news"])
    assert [c[0] for c in g.calls] == ["GET", "PATCH"]
    assert sleeps == []


async def test_modify_message_retries_a_412_once(sleeps):
    g = _Graph([412, 200], [[]])
    await _provider(g).modify_message("m1", add_labels=["READ"])
    assert [c[0] for c in g.calls] == ["PATCH", "PATCH"]
    assert sleeps == [0.5]


async def test_apply_flags_retries_a_412_once(sleeps):
    g = _Graph([412, 200], [[]])
    await _provider(g).apply_flags("m1", is_read=True)
    assert [c[0] for c in g.calls] == ["PATCH", "PATCH"]


async def test_apply_flags_does_not_retry_a_500(sleeps):
    g = _Graph([500], [[]])
    with pytest.raises(httpx.HTTPStatusError):
        await _provider(g).apply_flags("m1", is_read=True)
    assert len(g.calls) == 1
