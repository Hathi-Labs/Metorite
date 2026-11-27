"""Graph gateway faults (HRD-20, task 3224).

R7 fences named here:

* ``outlook-get-retry``: a GET that gets 502, 503 or 504 is tried again, twice
  at most, and ``Retry-After`` is honoured up to a cap.
* ``outlook-write-no-retry``: a POST that writes mail is sent once. A 502 or a
  read timeout raises ``ProviderDidNotAnswer`` with words that never say that
  nothing was sent.
* ``outlook-write-503``: the gateway maps that error to a 503 with those words.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from email_ingestion.providers import outlook as outlook_mod
from email_ingestion.providers.base import ProviderDidNotAnswer
from email_ingestion.providers.outlook import GRAPH_API_BASE, _guard_client


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(outlook_mod.asyncio, "sleep", fake_sleep)
    return waits


def _client(statuses: list[int], calls: list[httpx.Request],
            headers: dict[str, str] | None = None, raises=None,
            retry_reads=True):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if raises is not None:
            raise raises
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        return httpx.Response(
            status, json={"value": []}, headers=headers or {}, request=request)

    return _guard_client(httpx.AsyncClient(
        base_url=GRAPH_API_BASE, transport=httpx.MockTransport(handler)),
        retry_reads)


def _run(coro):
    return asyncio.run(coro)


def test_get_502_then_200_succeeds():
    calls: list[httpx.Request] = []
    client = _client([502, 200], calls)
    resp = _run(client.get("/me/mailFolders"))
    assert resp.status_code == 200
    assert len(calls) == 2


def test_get_tries_again_twice_at_most(_no_sleep):
    calls: list[httpx.Request] = []
    client = _client([503], calls)
    resp = _run(client.get("/me/mailFolders"))
    assert resp.status_code == 503
    assert len(calls) == 3
    assert _no_sleep == [0.5, 1.5]


def test_get_honours_retry_after_up_to_the_cap(_no_sleep):
    calls: list[httpx.Request] = []
    client = _client([504, 504, 200], calls, headers={"Retry-After": "99"})
    _run(client.get("/me/mailFolders"))
    assert _no_sleep == [outlook_mod._READ_RETRY_MAX_WAIT_SECS] * 2


def test_get_404_is_not_tried_again():
    calls: list[httpx.Request] = []
    resp = _run(_client([404], calls).get("/me/messages/x"))
    assert resp.status_code == 404
    assert len(calls) == 1


def test_send_502_is_one_call_and_clear_text():
    calls: list[httpx.Request] = []
    client = _client([502, 200], calls)
    with pytest.raises(ProviderDidNotAnswer) as err:
        _run(client.post("/me/messages/abc/reply", json={}))
    assert len(calls) == 1
    assert err.value.detail == (
        "Outlook did not answer. It may not have sent the mail. "
        "Check Sent before you try again.")
    assert "graph.microsoft.com" not in str(err.value)


def test_draft_create_502_is_one_call_and_clear_text():
    calls: list[httpx.Request] = []
    client = _client([502], calls)
    with pytest.raises(ProviderDidNotAnswer) as err:
        _run(client.post("/me/messages", json={}))
    assert len(calls) == 1
    assert err.value.detail == (
        "Outlook did not answer. It may not have saved the draft. "
        "Check Drafts before you try again.")


def test_send_read_timeout_gives_the_same_error():
    calls: list[httpx.Request] = []
    client = _client([200], calls, raises=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderDidNotAnswer):
        _run(client.post("/me/sendMail", json={}))
    assert len(calls) == 1


def test_a_post_that_is_not_mail_keeps_its_old_path():
    calls: list[httpx.Request] = []
    client = _client([502], calls)
    resp = _run(client.post("/me/mailFolders", json={}))
    assert resp.status_code == 502
    assert len(calls) == 1


def test_gateway_answers_503_with_the_words():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway import main as gw

    app = FastAPI()
    app.add_exception_handler(
        ProviderDidNotAnswer, gw._provider_did_not_answer)

    @app.post("/boom")
    async def boom():
        raise ProviderDidNotAnswer("Outlook", "send")

    resp = TestClient(app).post("/boom")
    assert resp.status_code == 503
    assert resp.json()["detail"] == (
        "Outlook did not answer. It may not have sent the mail. "
        "Check Sent before you try again.")


def test_the_model_reads_the_words():
    """A 503 with this detail reaches the model as text, not "Function failed"."""
    from acb_common.tool_refusals import (
        GatewayRefusal,
        RefusalVoice,
        refusal_text,
        safe_detail,
    )

    words = ProviderDidNotAnswer("Outlook", "send").detail
    detail, fields = safe_detail(503, {"detail": words, "code": "x"})
    text = refusal_text(
        GatewayRefusal("x", status=503, detail=detail, fields=fields,
                       code="provider_did_not_answer"),
        RefusalVoice(app="email app", log_event="email.tool_refused",
                     not_found_next="n"))
    assert text.startswith("Refused:")
    assert "Outlook did not answer. It may not have sent the mail." in text
    assert "fault on our side" not in text
    assert "check Sent or Drafts before you try again" in text
    # Every other 503 keeps its text.
    plain = refusal_text(
        GatewayRefusal("x", status=503, detail="busy"),
        RefusalVoice(app="email app", log_event="e", not_found_next="n"))
    assert "This is a fault on our side." in plain


def test_get_is_not_retried_unless_the_caller_opts_in():
    calls: list[httpx.Request] = []
    flag = {"on": False}
    client = _client([502, 200], calls, retry_reads=lambda: flag["on"])
    assert _run(client.get("/me/messages/x")).status_code == 502
    assert len(calls) == 1
    flag["on"] = True
    calls.clear()
    assert _run(client.get("/me/messages/x")).status_code == 200
    assert len(calls) == 2


@pytest.mark.parametrize("value,expected", [
    ("nan", 0.5), ("inf", 0.5), ("-5", 0.0), ("2", 2.0), ("99", 5.0)])
def test_a_bad_retry_after_is_clamped(_no_sleep, value, expected):
    calls: list[httpx.Request] = []
    client = _client([503, 200], calls, headers={"Retry-After": value})
    _run(client.get("/me/mailFolders"))
    assert _no_sleep == [expected]


def test_send_later_says_the_mail_may_have_gone_out():
    from gateway.routes.email.automation import send_later

    why = send_later._why(ProviderDidNotAnswer("Outlook", "send"))
    assert "may have gone out" in why
    assert "Check Sent" in why
    assert "ProviderDidNotAnswer" not in why
