"""Unit tests for the WhatsApp gateway route helpers + router assembly.

Covers the pure decision functions (webhook signature verification, the 24h
send-window guard) and asserts the whole route package imports and registers its
paths on the shared router — a cheap guard against an import/wiring regression.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

# ── webhook signature verification ────────────────────────────────────────────

def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_passes() -> None:
    from gateway.routes.whatsapp.transport.webhook import verify_signature
    body = b'{"entry": []}'
    assert verify_signature("s3cr3t", body, _sign("s3cr3t", body)) is True


def test_tampered_body_fails() -> None:
    from gateway.routes.whatsapp.transport.webhook import verify_signature
    sig = _sign("s3cr3t", b'{"entry": []}')
    assert verify_signature("s3cr3t", b'{"entry": [{"evil": 1}]}', sig) is False


def test_missing_or_malformed_header_fails_closed_with_secret() -> None:
    from gateway.routes.whatsapp.transport.webhook import verify_signature
    assert verify_signature("s3cr3t", b"x", None) is False
    assert verify_signature("s3cr3t", b"x", "md5=deadbeef") is False


def test_no_secret_configured_passes_with_warning() -> None:
    # Dev / self-host without WHATSAPP_APP_SECRET set: don't hard-block ingest.
    from gateway.routes.whatsapp.transport.webhook import verify_signature
    assert verify_signature(None, b"x", None) is True


# ── F8: the route refuses a missing secret outside dev (WS-20 WA-C1) ─────────

@pytest.fixture()
def _acb_env(monkeypatch: pytest.MonkeyPatch):
    """Set ``ACB_ENV`` for one test. ``get_settings`` is cached, so the cache
    is cleared before and after."""
    from acb_common import get_settings

    def _set(value: str) -> None:
        monkeypatch.setenv("ACB_ENV", value)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _webhook_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.whatsapp.transport.webhook import receive_webhook

    app = FastAPI()
    app.post("/whatsapp/webhook")(receive_webhook)
    return TestClient(app)


def test_no_secret_outside_dev_answers_403_before_any_read(
    monkeypatch: pytest.MonkeyPatch, _acb_env,
) -> None:
    """``ACB_ENV`` absent means ``prod``. No database is set up here, so a
    route that reached its account read would fail with a 500, not a 403."""
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    for env in ("prod", "staging"):
        _acb_env(env)
        resp = _webhook_client().post("/whatsapp/webhook", content=b'{"entry": []}')
        assert resp.status_code == 403, env


def test_no_secret_in_dev_accepts_the_post(
    monkeypatch: pytest.MonkeyPatch, _acb_env,
) -> None:
    """An empty batch carries no number, so the route answers 200 and reads
    nothing."""
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    _acb_env("dev")
    resp = _webhook_client().post("/whatsapp/webhook", content=b'{"entry": []}')
    assert resp.status_code == 200


# ── One unit for each Meta number in a batch (WA-C1 review P0) ───────────────

def _chg(pnid: str | None, wamid: str) -> dict:
    meta = {"phone_number_id": pnid} if pnid else {}
    return {"field": "messages", "value": {"metadata": meta, "messages": [{
        "from": "91", "id": wamid, "timestamp": "1790000000", "type": "text",
        "text": {"body": "hi"}}]}}


def test_split_by_number_gives_each_number_only_its_own_changes() -> None:
    from gateway.routes.whatsapp.transport.webhook import split_by_number
    from whatsapp_ingestion.providers.webhook import parse_webhook

    payload = {"object": "whatsapp_business_account", "entry": [
        {"id": "W1", "changes": [_chg("PA", "a1"), _chg("PB", "b1")]},
        {"id": "W2", "changes": [_chg("PA", "a2"), _chg(None, "x1")]},
    ]}
    groups, unrouted = split_by_number(payload)
    assert set(groups) == {"PA", "PB"}
    assert unrouted == 1
    assert [e["id"] for e in groups["PA"]["entry"]] == ["W1", "W2"]
    a = parse_webhook(groups["PA"])
    b = parse_webhook(groups["PB"])
    assert (a.phone_number_id, [m.wa_message_id for m in a.messages]) == (
        "PA", ["a1", "a2"])
    assert (b.phone_number_id, [m.wa_message_id for m in b.messages]) == (
        "PB", ["b1"])


@pytest.mark.parametrize("payload", [
    None, [], "x", {}, {"entry": None}, {"entry": ["x", None]},
    {"entry": [{"changes": "x"}]}, {"entry": [{"changes": [None, 3, {"value": 1}]}]},
    {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": 7}}}]}]},
])
def test_split_by_number_is_total(payload) -> None:
    from gateway.routes.whatsapp.transport.webhook import split_by_number

    groups, _ = split_by_number(payload)
    assert groups == {}


def test_a_failed_number_answers_500_after_the_others_ran(
    monkeypatch: pytest.MonkeyPatch, _acb_env,
) -> None:
    """Meta then sends the batch again. The persist path is idempotent, so
    the numbers that landed write nothing new."""
    import json

    from gateway.routes.whatsapp.transport import webhook

    ran: list[str] = []

    async def _ingest(pnid, sub_payload):
        ran.append(pnid)
        if pnid == "PA":
            raise RuntimeError("db down")

    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    _acb_env("dev")
    monkeypatch.setattr(webhook, "_ingest_number", _ingest)
    body = json.dumps({"entry": [
        {"id": "W1", "changes": [_chg("PA", "a1")]},
        {"id": "W2", "changes": [_chg("PB", "b1")]},
    ]}).encode()
    resp = _webhook_client().post("/whatsapp/webhook", content=body)
    assert resp.status_code == 500
    assert ran == ["PA", "PB"]


# ── 24h send window + regime ──────────────────────────────────────────────────

def test_window_open_only_before_expiry() -> None:
    from gateway.routes.whatsapp.transport.send import window_is_open
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    assert window_is_open(now + timedelta(hours=1), now) is True
    assert window_is_open(now - timedelta(hours=1), now) is False
    assert window_is_open(None, now) is False


def test_text_inside_window_is_session_send() -> None:
    from gateway.routes.whatsapp.transport.send import SendRequest, choose_regime
    assert choose_regime(SendRequest(text="hi"), window_open=True) == "session"


def test_text_outside_window_is_blocked() -> None:
    from gateway.routes.whatsapp.transport.send import SendRequest, choose_regime
    with pytest.raises(HTTPException) as exc:
        choose_regime(SendRequest(text="hi"), window_open=False)
    assert exc.value.status_code == 409  # must use a template


def test_template_always_allowed_even_when_window_closed() -> None:
    from gateway.routes.whatsapp.transport.send import SendRequest, choose_regime
    req = SendRequest(template_name="payment_reminder")
    assert choose_regime(req, window_open=False) == "template"


def test_empty_send_is_rejected() -> None:
    from gateway.routes.whatsapp.transport.send import SendRequest, choose_regime
    with pytest.raises(HTTPException) as exc:
        choose_regime(SendRequest(), window_open=True)
    assert exc.value.status_code == 400


# ── router assembly ───────────────────────────────────────────────────────────

def test_router_registers_expected_paths() -> None:
    from gateway.routes.whatsapp import router
    paths = {r.path for r in router.routes}
    for expected in (
        "/whatsapp/accounts",
        "/whatsapp/streams",
        "/whatsapp/chats",
        "/whatsapp/chats/{chat_id}/messages",
        "/whatsapp/chats/{chat_id}/send",
        "/whatsapp/search",
        "/whatsapp/webhook",
    ):
        assert expected in paths, f"missing route {expected}"


# ── WS-20 WA-C3 fix round P2-2: when the webhook defers the chat sweep ───────

@pytest.mark.parametrize(("counts", "has_progress", "deferred"), [
    ({"messages": 0, "history_messages": 40, "history_complete": 0}, True, True),
    ({"messages": 0, "history_messages": 0, "history_complete": 0}, True, True),
    ({"messages": 0, "history_messages": 40, "history_complete": 1}, True, False),
    ({"messages": 2, "history_messages": 40, "history_complete": 0}, True, False),
    ({"messages": 0, "history_messages": 0, "history_complete": 0}, False, False),
    ({"messages": 1, "chats": 1}, False, False),
])
def test_only_a_history_batch_of_an_open_import_defers_the_sweep(
    counts, has_progress, deferred,
) -> None:
    from gateway.routes.whatsapp.transport.webhook import defer_chat_sweep

    assert defer_chat_sweep(counts, has_progress=has_progress) is deferred
