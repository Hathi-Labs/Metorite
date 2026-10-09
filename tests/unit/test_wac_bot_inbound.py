"""WS-47 WAC-2 — the bot number's branch of the Meta webhook, database-free.

Spec: ``project-docs/specs/whatsapp_assistant_channel.md`` §5.4 ("Link
redemption, as WAC-2 builds it"), §5.9 and §9.

The REAL ``receive_webhook`` route runs here. The two cross-tenant reads, the
tenant session and the Cloud API provider are fakes, so this suite proves
the branch logic. ``test_wac_bot_link_r8.py`` proves the SQL on a real
database (R8).

R7 fences named here:

* ``wac-unknown-sender-fixed-reply``: the reply to an unlinked phone is the
  fixed string of §5.4, word for word, and holds no org data.
* ``wac-bot-fails-closed``: with no ``WHATSAPP_APP_SECRET`` the bot group
  does nothing, in dev too.
* ``wac-bot-dark``: switch off, or no token: 200, no write, no send. With no
  bot number id, the group stays on the WS-20 path.
* ``wac-sender-limit``: the sixth failed code from one phone in 15 minutes
  gets the failure reply with no lookup.
* ``wac-ws20-unchanged``: in a mixed batch, only the other numbers reach
  ``_ingest_number``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Any

import pytest
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.whatsapp.transport import webhook
from gateway.routes.whatsapp_channel import inbound, link

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "22222222-2222-4222-8222-222222222222"
BOT = "1098765432"
OTHER = "2098765432"
PHONE = "919990000001"
TOKEN = "EAAG-secret-bot-token"
SECRET = "wac2-app-secret"
CODE = "7K3M9QX2AB"
ROW_ID = "33333333-3333-4333-8333-333333333333"
MEMBER = "alice@fracktal.in"

#: The spec's texts, written out here rather than imported, so a change to a
#: constant in the module fails this suite (§9: "the fixed string").
UNKNOWN_TEXT = ("Hi, this is Metorite. To chat with your workspace, open My "
                "Profile in Metorite and select Chat on WhatsApp.")
FAILED_TEXT = ("That link code did not work. Open My Profile in Metorite and "
               "get a new link.")
OTHER_PERSON_TEXT = ("This phone is already linked to another Metorite "
                     "account. Unlink it there first.")


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _World:
    """The fake database: the two discovery reads and the bound session."""

    def __init__(self) -> None:
        self.code_rows: list[dict[str, Any]] = []
        self.phone_links: list[dict[str, Any]] = []
        self.pending_held = True
        #: What the row state read answers after a failed locked re-check.
        self.row_state: dict[str, Any] | None = None
        self.code_reads: list[tuple[str, str]] = []
        self.phone_reads: list[str] = []
        self.sessions = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sent: list[tuple[str, str]] = []
        self.ingested: list[str] = []

    async def code_rows_for(self, hashed: str, wa_id: str) -> list[dict[str, Any]]:
        self.code_reads.append((hashed, wa_id))
        return list(self.code_rows)

    async def links_for(self, wa_id: str) -> list[dict[str, Any]]:
        self.phone_reads.append(wa_id)
        return list(self.phone_links)

    def session(self):
        world = self

        @asynccontextmanager
        async def _session(organization_id: str | None = None):
            from gateway.db import current_tenant

            assert organization_id is None, "the redemption binds from context"
            world.sessions += 1
            world.bound = current_tenant()
            yield world

        return _session

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))
        if sql.startswith("SELECT status, wa_id FROM whatsapp_member_links"):
            return _Result([self.row_state] if self.row_state else [])
        if "FOR UPDATE" in sql:
            return _Result([{"id": ROW_ID}] if self.pending_held else [])
        if "whatsapp_member_links_for_phone" in sql:
            return _Result(list(self.phone_links))
        if "FROM organization o" in sql:
            return _Result([{"org_name": "Fracktal Works",
                             "member_name": "Alice Rao"}])
        return _Result([])

    def writes(self) -> list[str]:
        return [sql for sql, _ in self.calls if sql.startswith("UPDATE")]


class _Provider:
    def __init__(self, world: _World, creds: dict[str, Any]) -> None:
        self.world = world
        assert creds == {"access_token": TOKEN, "phone_number_id": BOT}

    async def send_text(self, to: str, body: str) -> str:
        self.world.sent.append((to, body))
        return "wamid.out"


@pytest.fixture()
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    s = get_settings()
    monkeypatch.setattr(s, "whatsapp_assistant_enabled", True, raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_orgs", f"{ORG_A},{ORG_B}",
                        raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_phone_number_id", BOT,
                        raising=False)
    monkeypatch.setattr(s, "whatsapp_assistant_access_token", TOKEN,
                        raising=False)
    monkeypatch.setattr(s, "acb_env", "prod", raising=False)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)

    w = _World()
    monkeypatch.setattr(inbound, "_code_rows", w.code_rows_for)
    monkeypatch.setattr(inbound, "_active_links_for_phone", w.links_for)
    monkeypatch.setattr(inbound, "tenant_session", w.session())
    monkeypatch.setattr(inbound, "_FAILED", {})
    monkeypatch.setattr(inbound, "_HANDLED", OrderedDict())

    from whatsapp_ingestion.providers import factory

    def _build(name: str, creds: dict[str, Any]) -> _Provider:
        assert name == "cloud_api"
        return _Provider(w, creds)

    monkeypatch.setattr(factory, "build_provider", _build)

    async def _ingest(pnid: str, sub: dict[str, Any]) -> None:
        w.ingested.append(pnid)

    monkeypatch.setattr(webhook, "_ingest_number", _ingest)
    return w


def _message(body: str, *, pnid: str = BOT, sender: str = PHONE,
             mtype: str = "text", wamid: str | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"from": sender,
                           "id": wamid or f"wamid.{uuid.uuid4().hex}",
                           "timestamp": "1790000000", "type": mtype}
    if mtype == "text":
        msg["text"] = {"body": body}
    elif mtype == "interactive":
        msg["interactive"] = {"type": "button_reply",
                              "button_reply": {"id": "x", "title": body}}
    return {"field": "messages", "value": {
        "metadata": {"display_phone_number": "919800000000",
                     "phone_number_id": pnid},
        "contacts": [{"profile": {"name": "Alice"}, "wa_id": sender}],
        "messages": [msg],
    }}


def _status(pnid: str = BOT) -> dict[str, Any]:
    return {"field": "messages", "value": {
        "metadata": {"phone_number_id": pnid},
        "statuses": [{"id": "wamid.out", "status": "delivered",
                      "timestamp": "1790000000", "recipient_id": PHONE}],
    }}


def _body(*changes: dict[str, Any]) -> bytes:
    return json.dumps({"object": "whatsapp_business_account", "entry": [
        {"id": "WABA", "changes": list(changes)}]}).encode("utf-8")


def _signature(raw: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _post(*changes: dict[str, Any], sign: bool = True) -> Any:
    raw = _body(*changes)
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = _signature(raw)
    app = FastAPI()
    app.post("/whatsapp/webhook")(webhook.receive_webhook)
    return TestClient(app).post("/whatsapp/webhook", content=raw, headers=headers)


def _pending(org: str = ORG_A) -> dict[str, Any]:
    return {"id": ROW_ID, "organization_id": org, "member_email": MEMBER,
            "status": "pending", "code_expires_at": None}


# ── The code text ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Link me: 7K3M9QX2AB", "link me:7k3m9qx2ab", "  LINK ME :  7K3M-9QX2-AB ",
    "Link me: 7K3M9QX2AB\n",
])
def test_the_link_message_is_read_in_any_case_and_spacing(text: str) -> None:
    rest = inbound.link_code_attempt(text)
    assert rest is not None
    assert inbound.normalise_code(rest) == CODE


def test_crockford_letters_map_to_digits_before_the_hash() -> None:
    assert inbound.normalise_code("O1IL23456A") == "011123456A"
    assert link.code_hash(inbound.normalise_code("oiL0000000") or "") == \
        link.code_hash("0110000000")


@pytest.mark.parametrize("rest", ["", "7K3M9QX2A", "7K3M9QX2ABC", "7K3M9QX2AU"])
def test_a_code_of_the_wrong_shape_is_no_code(rest: str) -> None:
    assert inbound.normalise_code(rest) is None


def test_a_message_without_the_prefix_is_not_a_link_message() -> None:
    assert inbound.link_code_attempt("hello 7K3M9QX2AB") is None


# ── wac-unknown-sender-fixed-reply ──────────────────────────────────────────

def test_an_unlinked_phone_without_a_code_gets_exactly_the_fixed_reply(
    world: _World,
) -> None:
    res = _post(_message("What is due today?"))

    assert res.status_code == 200
    assert world.sent == [(PHONE, UNKNOWN_TEXT)]
    assert world.sessions == 0 and world.calls == [], "the reply wrote a row"
    assert world.code_reads == []
    for secret in (ORG_A, ORG_B, "Fracktal", MEMBER):
        assert secret not in world.sent[0][1]


def test_a_linked_phone_without_a_code_gets_no_reply_and_no_run(
    world: _World,
) -> None:
    world.phone_links = [{"organization_id": ORG_A, "member_email": MEMBER,
                          "is_current": True}]
    res = _post(_message("What is due today?"))
    assert res.status_code == 200
    assert world.sent == [] and world.sessions == 0


def test_a_button_tap_gets_no_action(world: _World) -> None:
    res = _post(_message("Confirm", mtype="interactive"))
    assert res.status_code == 200
    assert world.sent == [] and world.phone_reads == [] and world.code_reads == []


def test_a_status_update_is_logged_and_nothing_else(world: _World) -> None:
    res = _post(_status())
    assert res.status_code == 200
    assert world.sent == [] and world.sessions == 0 and world.ingested == []


# ── The redemption ──────────────────────────────────────────────────────────

def test_a_link_message_activates_the_row_and_sends_the_success_reply(
    world: _World,
) -> None:
    world.code_rows = [_pending()]
    res = _post(_message(f"Link me: {CODE.lower()}"))

    assert res.status_code == 200
    assert world.code_reads == [(link.code_hash(CODE), PHONE)]
    assert world.bound == ORG_A, "the write ran outside the code's tenant"
    (activate,) = [p for sql, p in world.calls
                   if sql.startswith("UPDATE") and "'active'" in sql]
    assert activate == {"id": ROW_ID, "wa": PHONE, "cur": True}
    assert world.sent == [(PHONE, "Linked to Fracktal Works as Alice Rao. Ask "
                                  "me about your tasks, projects or calendar.")]
    lock = world.calls[0][0]
    assert "pg_advisory_xact_lock" in lock and "wac_link_phone" in lock


def test_the_same_person_in_a_second_org_links_without_current(
    world: _World,
) -> None:
    world.code_rows = [_pending(ORG_B)]
    world.phone_links = [{"organization_id": ORG_A, "member_email": MEMBER.upper(),
                          "is_current": True}]
    _post(_message(f"Link me: {CODE}"))
    (activate,) = [p for sql, p in world.calls if "'active'" in sql]
    assert activate["cur"] is False
    assert world.sent[0][1].startswith("Linked to ")


def test_a_second_person_on_a_linked_phone_links_nothing(world: _World) -> None:
    world.code_rows = [_pending()]
    world.phone_links = [{"organization_id": ORG_B,
                          "member_email": "bob@fracktal.in", "is_current": True}]
    _post(_message(f"Link me: {CODE}"))
    assert world.writes() == []
    assert world.sent == [(PHONE, OTHER_PERSON_TEXT)]


def test_a_redelivered_link_message_gets_the_success_reply_and_no_write(
    world: _World,
) -> None:
    world.code_rows = [dict(_pending(), status="active")]
    _post(_message(f"Link me: {CODE}"))
    assert world.writes() == []
    assert world.sent[0][1].startswith("Linked to Fracktal Works as Alice Rao.")


@pytest.mark.parametrize("case", ["no_row", "stale"])
def test_a_wrong_used_or_expired_code_links_nothing(world: _World, case) -> None:
    if case == "stale":
        # The row was used or expired between the read and the lock.
        world.code_rows = [_pending()]
        world.pending_held = False
    _post(_message(f"Link me: {CODE}"))
    assert world.writes() == []
    assert world.sent == [(PHONE, FAILED_TEXT)]


def test_a_code_of_the_wrong_shape_gets_the_failure_reply_with_no_lookup(
    world: _World,
) -> None:
    _post(_message("Link me: nope"))
    assert world.code_reads == []
    assert world.sent == [(PHONE, FAILED_TEXT)]


def test_an_org_that_is_not_on_the_list_links_nothing(world: _World,
                                                     monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_orgs", ORG_B,
                        raising=False)
    world.code_rows = [_pending(ORG_A)]
    _post(_message(f"Link me: {CODE}"))
    assert world.sessions == 0
    assert world.sent == [(PHONE, FAILED_TEXT)]


def test_a_concurrent_copy_from_the_same_phone_gets_the_success_reply(
    world: _World,
) -> None:
    """Both copies read the row pending. The first links it. The second finds
    no pending row under the lock, and the row is active for this phone."""
    world.code_rows = [_pending()]
    world.pending_held = False
    world.row_state = {"status": "active", "wa_id": PHONE}
    _post(_message(f"Link me: {CODE}"))
    assert world.writes() == []
    assert world.sent[0][1].startswith("Linked to Fracktal Works as Alice Rao.")
    assert inbound._FAILED == {}, "a redelivery counted as a failed code"


def test_a_used_code_from_another_phone_is_still_a_failure(world: _World) -> None:
    world.code_rows = [_pending()]
    world.pending_held = False
    world.row_state = {"status": "active", "wa_id": "919990000009"}
    _post(_message(f"Link me: {CODE}"))
    assert world.sent == [(PHONE, FAILED_TEXT)]


# ── wac-redelivery-record ───────────────────────────────────────────────────

def test_a_redelivered_failed_code_counts_once(world: _World) -> None:
    """A 500 for another group makes Meta send the whole batch again."""
    for _ in range(6):
        _post(_message(f"Link me: {CODE}", wamid="wamid.same"))
    assert len(world.code_reads) == 1
    assert world.sent == [(PHONE, FAILED_TEXT)] * 6
    assert not inbound.is_limited(PHONE), "one message counted six times"


def test_a_limited_sender_still_gets_the_success_reply_on_a_redelivery(
    world: _World,
) -> None:
    world.code_rows = [_pending()]
    _post(_message(f"Link me: {CODE}", wamid="wamid.good"))
    assert world.sent[-1][1].startswith("Linked to ")
    world.code_rows = []
    for _ in range(5):
        _post(_message(f"Link me: {CODE}"))
    assert inbound.is_limited(PHONE)
    reads = len(world.code_reads)

    _post(_message(f"Link me: {CODE}", wamid="wamid.good"))
    assert world.sent[-1][1].startswith("Linked to Fracktal Works as Alice Rao.")

    _post(_message(f"Link me: {CODE}"))  # A NEW message: limited.
    assert world.sent[-1] == (PHONE, FAILED_TEXT)
    assert len(world.code_reads) == reads, "a redelivery or a limited code read"


def test_the_redelivery_record_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(inbound, "_HANDLED", OrderedDict())
    monkeypatch.setattr(inbound, "_HANDLED_CAP", 3)
    reply = inbound.Reply(PHONE, FAILED_TEXT, "failed")
    for i in range(5):
        inbound.remember_reply(PHONE, f"w{i}", reply)
    assert list(inbound._HANDLED) == [(PHONE, "w2"), (PHONE, "w3"), (PHONE, "w4")]
    assert inbound.handled_reply(PHONE, "w0") is None
    assert inbound.handled_reply("919990000009", "w4") is None


# ── wac-sender-limit ────────────────────────────────────────────────────────

def test_the_sixth_failed_code_in_15_minutes_gets_no_lookup(world: _World) -> None:
    for _ in range(5):
        _post(_message(f"Link me: {CODE}"))
    assert len(world.code_reads) == 5

    world.code_rows = [_pending()]  # Even a good code: the sender is limited.
    _post(_message(f"Link me: {CODE}"))

    assert len(world.code_reads) == 5, "the sixth attempt ran a lookup"
    assert world.sessions == 0
    assert world.sent[-1] == (PHONE, FAILED_TEXT)


def test_the_limit_is_per_sender(world: _World) -> None:
    for _ in range(5):
        _post(_message(f"Link me: {CODE}"))
    _post(_message(f"Link me: {CODE}", sender="919990000002"))
    assert len(world.code_reads) == 6


def test_the_window_forgets_old_failures(world: _World, monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(inbound.time, "monotonic", lambda: clock[0])
    for _ in range(5):
        inbound.record_failure(PHONE)
    assert inbound.is_limited(PHONE)
    clock[0] += inbound.FAILED_WINDOW_S + 1
    assert not inbound.is_limited(PHONE)


# ── wac-bot-fails-closed ────────────────────────────────────────────────────

@pytest.mark.parametrize("env", ["dev", "prod"])
def test_no_app_secret_makes_the_bot_group_do_nothing(world: _World, monkeypatch,
                                                      env: str) -> None:
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    monkeypatch.setattr(get_settings(), "acb_env", env, raising=False)
    world.code_rows = [_pending()]

    res = _post(_message(f"Link me: {CODE}"), sign=False)

    # Outside dev the route refuses every body (F8). In dev it accepts, and
    # the bot group still does nothing.
    assert res.status_code == (200 if env == "dev" else 403)
    assert world.code_reads == [] and world.phone_reads == []
    assert world.sessions == 0 and world.sent == []


def test_no_app_secret_in_dev_keeps_the_ws20_path(world: _World,
                                                  monkeypatch) -> None:
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    monkeypatch.setattr(get_settings(), "acb_env", "dev", raising=False)
    res = _post(_message("hi", pnid=OTHER), sign=False)
    assert res.status_code == 200 and world.ingested == [OTHER]


# ── wac-bot-dark ────────────────────────────────────────────────────────────

def test_the_switch_off_acks_and_drops_the_bot_group(world: _World,
                                                     monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_enabled", False,
                        raising=False)
    world.code_rows = [_pending()]
    res = _post(_message(f"Link me: {CODE}"), _message("hi", sender="919990000003"))
    assert res.status_code == 200
    assert world.code_reads == [] and world.phone_reads == []
    assert world.sessions == 0 and world.sent == []
    assert world.ingested == [], "the bot group reached the WS-20 path"


def test_no_token_acks_and_drops_the_bot_group(world: _World, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_access_token", "",
                        raising=False)
    res = _post(_message("hi"))
    assert res.status_code == 200
    assert world.phone_reads == [] and world.sent == [] and world.ingested == []


def test_no_bot_number_id_leaves_every_group_on_the_ws20_path(
    world: _World, monkeypatch,
) -> None:
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_phone_number_id", "",
                        raising=False)
    res = _post(_message(f"Link me: {CODE}"))
    assert res.status_code == 200
    assert world.ingested == [BOT]
    assert world.code_reads == [] and world.sent == []


def test_a_malformed_bot_number_id_reads_as_unset(monkeypatch) -> None:
    from gateway.routes.whatsapp_channel import flags

    monkeypatch.setattr(get_settings(), "whatsapp_assistant_phone_number_id",
                        "12/34", raising=False)
    assert flags.bot_phone_number_id() is None
    assert flags.bot_credentials() is None


# ── wac-ws20-unchanged ──────────────────────────────────────────────────────

def test_a_mixed_batch_sends_only_the_other_numbers_to_ws20(world: _World) -> None:
    res = _post(_message("hi", pnid=OTHER), _message("What is due?"),
                _status(OTHER))
    assert res.status_code == 200
    assert world.ingested == [OTHER]
    assert world.sent == [(PHONE, UNKNOWN_TEXT)]


def test_a_failed_ws20_group_answers_500_and_sends_no_bot_reply(
    world: _World, monkeypatch,
) -> None:
    async def _boom(pnid: str, sub: dict[str, Any]) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(webhook, "_ingest_number", _boom)
    res = _post(_message("hi", pnid=OTHER), _message("What is due?"))
    # Meta sends the batch again, and the reply comes with the retry.
    assert res.status_code == 500
    assert world.sent == []


def test_a_failed_bot_group_logs_the_error_class_and_no_message(
    world: _World, monkeypatch,
) -> None:
    from structlog.testing import capture_logs

    async def _boom(hashed: str, wa_id: str) -> list[dict[str, Any]]:
        raise RuntimeError(f"DETAIL: Key (wa_id)=({PHONE}) already exists.")

    monkeypatch.setattr(inbound, "_code_rows", _boom)
    with capture_logs() as logs:
        res = _post(_message(f"Link me: {CODE}"))
    assert res.status_code == 500
    (line,) = [e for e in logs if e["event"] == "whatsapp.webhook.bot_group_failed"]
    assert line["error_type"] == "RuntimeError" and "error" not in line
    assert PHONE not in repr(logs)
    assert world.sent == []


# ── The send ────────────────────────────────────────────────────────────────

async def test_the_replies_go_out_only_in_the_background_task_after_the_200(
    world: _World,
) -> None:
    from starlette.requests import Request

    raw = _body(_message("What is due today?"))
    delivered = False

    async def _receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request({
        "type": "http", "method": "POST", "path": "/whatsapp/webhook",
        "query_string": b"", "headers": [
            (b"content-type", b"application/json"),
            (b"x-hub-signature-256", _signature(raw).encode()),
        ]}, _receive)

    response = await webhook.receive_webhook(request)

    assert response.status_code == 200
    assert world.sent == [], "a reply went out before the 200"
    assert response.background is not None
    await response.background()
    assert world.sent == [(PHONE, UNKNOWN_TEXT)]



async def test_a_failed_send_logs_no_exception_text(monkeypatch) -> None:
    import httpx
    from structlog.testing import capture_logs
    from whatsapp_ingestion.providers import factory

    monkeypatch.setattr(get_settings(), "whatsapp_assistant_phone_number_id",
                        BOT, raising=False)
    monkeypatch.setattr(get_settings(), "whatsapp_assistant_access_token",
                        TOKEN, raising=False)

    class _Failing:
        async def send_text(self, to: str, body: str) -> str:
            req = httpx.Request("POST", f"https://graph.example/{TOKEN}")
            raise httpx.HTTPStatusError(
                f"boom {TOKEN}", request=req,
                response=httpx.Response(401, request=req, json={
                    "error": {"code": 190, "type": "OAuthException"}}))

    monkeypatch.setattr(factory, "build_provider", lambda n, c: _Failing())
    with capture_logs() as logs:
        await inbound.send_replies([inbound.Reply(PHONE, UNKNOWN_TEXT, "unknown")])

    (line,) = [e for e in logs if e["event"] == "whatsapp_channel.bot.reply_failed"]
    assert line["status"] == 401 and line["meta_code"] == 190
    assert TOKEN not in repr(logs)
    assert PHONE not in repr(logs), "a log line carries the whole phone number"
