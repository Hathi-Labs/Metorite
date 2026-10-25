"""WS-66 PN-1, the hermetic half: web push with no database.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §10.2, and
``chat_run_continuity.md`` §S7. The SQL half is
``tests/unit/test_push_subscriptions.py`` (R8, real Postgres). This half
needs no database, so the deploy run proves it too.

* **The flag.** Off, each ``/push`` route answers 404. ``/version`` carries
  ``web_push``.
* **The keys.** A pair that does not match reads as not configured.
* **The endpoint rule.** Only the known push services, on https and port 443.
* **The body.** No member, no org, and no other field: a 422.
* **The payload.** The test push has a fixed title and no body. It equals
  ``tests/fixtures/push_test_payload.json``, which ``src/lib/push.test.ts``
  reads too.
* **The send.** A 404 or a 410 reads as ``gone``, other answers as
  ``failed``. The session follows no redirect and reads no proxy.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.push import endpoints, flags, routes, sender, store

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "push_test_payload.json"
ORG = "11111111-1111-4111-8111-111111111111"
EMAIL = "member@pn1.test"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/pn1-device-1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def vapid_pair() -> tuple[str, str]:
    """An ephemeral VAPID pair: (raw private scalar, uncompressed public)."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64(private), _b64(public)


def device_keys() -> dict[str, str]:
    """The keys of a browser subscription: a P-256 point and 16 bytes."""
    key = ec.generate_private_key(ec.SECP256R1())
    point = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"p256dh": _b64(point), "auth": _b64(b"0123456789abcdef")}


@pytest.fixture
def push_on(monkeypatch):
    """The flag on, with an ephemeral key pair. Never a real key."""
    private, public = vapid_pair()
    s = get_settings()
    monkeypatch.setattr(s, "web_push_enabled", True)
    monkeypatch.setattr(s, "vapid_private_key", private)
    monkeypatch.setattr(s, "vapid_public_key", public)
    monkeypatch.setattr(s, "vapid_subject", "mailto:push@pn1.test")
    return SimpleNamespace(private=private, public=public)


@pytest.fixture
def push_off(monkeypatch):
    monkeypatch.setattr(get_settings(), "web_push_enabled", False)


def _client(*, org: str | None = ORG, email: str | None = EMAIL) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(["feature:chat"]),
        organization_id=org)
    return TestClient(app)


def _body(**extra) -> dict:
    return {"endpoint": ENDPOINT, "expirationTime": None, "keys": device_keys(), **extra}


# ── The flag ─────────────────────────────────────────────────────────────────


class TestTheFlag:
    def test_it_is_off_by_default(self):
        from acb_common.settings import Settings

        fields = Settings.model_fields
        assert fields["web_push_enabled"].default is False
        for name in ("vapid_public_key", "vapid_private_key", "vapid_subject"):
            assert fields[name].default == ""

    @pytest.mark.parametrize("method,path", [
        ("GET", "/push/public-key"),
        ("POST", "/push/subscriptions"),
        ("DELETE", "/push/subscriptions"),
        ("POST", "/push/test"),
    ])
    def test_off_each_route_answers_404_and_touches_no_store(
            self, push_off, monkeypatch, method, path):
        def refuse(*_a, **_k):
            raise AssertionError("the store must not run with the flag off")

        for name in ("save", "remove", "devices", "record"):
            monkeypatch.setattr(store, name, refuse)
        for body in (None, _body(), {"endpoint": ENDPOINT}, {"junk": 1}):
            res = _client().request(method, path, json=body)
            assert res.status_code == 404, (body, res.text)
            assert res.json() == {"detail": "Not found"}

    def test_off_a_member_without_feature_chat_gets_404_too(self, push_off):
        # The flag is checked before the feature gate: a closed feature looks
        # like no feature to everybody.
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_current_user] = lambda: UserContext(
            email=EMAIL, role=UserRole.EMPLOYEE, access=build_access(["feature:email"]),
            organization_id=ORG)
        assert TestClient(app).get("/push/public-key").status_code == 404

    def test_a_member_without_feature_chat_gets_403(self, push_on):
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[get_current_user] = lambda: UserContext(
            email=EMAIL, role=UserRole.EMPLOYEE, access=build_access(["feature:email"]),
            organization_id=ORG)
        assert TestClient(app).get("/push/public-key").status_code == 403

    def test_a_session_with_no_org_gets_404_and_no_member_gets_401(self, push_on):
        assert _client(org=None).get("/push/public-key").status_code == 404
        # The feature gate refuses a caller with no member before the route.
        assert _client(email=None).get("/push/public-key").status_code == 401

    def test_version_carries_web_push(self, push_on, monkeypatch):
        import asyncio

        from gateway import main

        assert asyncio.run(main.version()).web_push is True
        monkeypatch.setattr(get_settings(), "web_push_enabled", False)
        body = asyncio.run(main.version()).model_dump()
        assert body["web_push"] is False
        assert not any("vapid" in k for k in body)


# ── The keys ─────────────────────────────────────────────────────────────────


class TestTheKeys:
    def test_a_matching_pair_is_configured_and_the_route_serves_the_public_key(self, push_on):
        assert flags.configured()
        res = _client().get("/push/public-key")
        assert res.status_code == 200
        assert res.json() == {"public_key": push_on.public}
        assert push_on.private not in res.text

    def test_a_pair_that_does_not_match_is_not_configured(self, push_on, monkeypatch):
        _other_private, other_public = vapid_pair()
        monkeypatch.setattr(get_settings(), "vapid_public_key", other_public)
        assert flags.vapid() is None and not flags.configured()
        assert _client().get("/push/public-key").status_code == 503
        assert _client().post("/push/test").status_code == 503

    def test_a_private_key_that_does_not_read_is_not_configured(self, push_on, monkeypatch):
        monkeypatch.setattr(get_settings(), "vapid_private_key", "not-a-key")
        assert not flags.configured()

    @pytest.mark.parametrize("value,ok", [
        ("mailto:ops@metorite.com", True),
        ("https://metorite.com", True),
        ("ops@metorite.com", False),
        ("mailto:", False),
        ("http://metorite.com", False),
        ("", False),
    ])
    def test_the_subject_is_mailto_or_https(self, push_on, monkeypatch, value, ok):
        monkeypatch.setattr(get_settings(), "vapid_subject", value)
        assert (flags.subject() is not None) is ok
        assert flags.configured() is ok


# ── The endpoint rule ────────────────────────────────────────────────────────


class TestTheEndpointRule:
    @pytest.mark.parametrize("url", [
        "https://fcm.googleapis.com/fcm/send/abc:def",
        "https://fcm.googleapis.com/wp/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://web.push.apple.com/QAbc",
        "https://wns2-par02p.notify.windows.com/w/?token=abc",
        "https://fcm.googleapis.com:443/fcm/send/abc",
    ])
    def test_a_known_push_service_passes(self, url):
        assert endpoints.endpoint_refusal(url) is None

    @pytest.mark.parametrize("url,why", [
        ("http://fcm.googleapis.com/fcm/send/abc", "endpoint is not https"),
        ("https://fcm.googleapis.com:8443/x", "endpoint port"),
        ("https://user:pw@fcm.googleapis.com/x", "bad endpoint"),
        ("https://10.0.0.5/admin", "not a known push service"),
        ("https://127.0.0.1/x", "not a known push service"),
        ("https://localhost/x", "not a known push service"),
        ("https://fcm.googleapis.com.evil.test/x", "not a known push service"),
        ("https://evilfcm.googleapis.com/x", "not a known push service"),
        ("https://push.apple.com.evil.test/x", "not a known push service"),
        ("https://fcm.googleapis.com/a\\b", "bad endpoint"),
        ("https://fcm.googleapis.com/a\nb", "bad endpoint"),
        ("https://fcm.googleapis.com/" + "a" * 2048, "endpoint too long"),
        ("", "no endpoint"),
    ])
    def test_anything_else_is_refused(self, url, why):
        assert endpoints.endpoint_refusal(url) == why

    def test_the_route_refuses_an_unknown_host_before_the_store(self, push_on, monkeypatch):
        def refuse(*_a, **_k):
            raise AssertionError("a refused endpoint must not reach the store")

        monkeypatch.setattr(store, "save", refuse)
        res = _client().post("/push/subscriptions", json=_body(endpoint="https://10.0.0.5/x"))
        assert res.status_code == 422
        assert "not a known push service" in res.text


# ── The body ─────────────────────────────────────────────────────────────────


class TestTheBody:
    @pytest.mark.parametrize("field", ["member_email", "organization_id", "email", "org"])
    def test_a_body_that_names_a_member_or_an_org_is_refused(self, push_on, monkeypatch, field):
        monkeypatch.setattr(store, "save", lambda *_a, **_k: pytest.fail("no save"))
        res = _client().post("/push/subscriptions", json=_body(**{field: "b@other.test"}))
        assert res.status_code == 422

    def test_the_delete_body_takes_the_endpoint_only(self, push_on, monkeypatch):
        monkeypatch.setattr(store, "remove", lambda *_a, **_k: pytest.fail("no remove"))
        res = _client().request("DELETE", "/push/subscriptions",
                                json={"endpoint": ENDPOINT, "member_email": "b@other.test"})
        assert res.status_code == 422

    @pytest.mark.parametrize("keys,why", [
        ({"p256dh": _b64(b"\x04" + b"x" * 63), "auth": _b64(b"0" * 16)}, "keys.p256dh"),
        ({"p256dh": _b64(b"\x02" + b"x" * 64), "auth": _b64(b"0" * 16)}, "keys.p256dh"),
        ({"p256dh": "not base64!", "auth": _b64(b"0" * 16)}, "keys.p256dh"),
        ({"p256dh": _b64(b"\x04" + b"x" * 64), "auth": _b64(b"0" * 15)}, "keys.auth"),
    ])
    def test_bad_keys_are_refused(self, push_on, monkeypatch, keys, why):
        monkeypatch.setattr(store, "save", lambda *_a, **_k: pytest.fail("no save"))
        res = _client().post("/push/subscriptions", json={"endpoint": ENDPOINT, "keys": keys})
        assert res.status_code == 422 and why in res.text

    def test_the_session_member_and_org_reach_the_store(self, push_on, monkeypatch):
        seen = {}

        async def save(org, member, sub):
            seen.update(org=org, member=member, sub=sub)
            return "row-1"

        monkeypatch.setattr(store, "save", save)
        res = _client(email="Member@PN1.test").post(
            "/push/subscriptions",
            json=_body(expirationTime=1_900_000_000_000, user_agent="x" * 900))
        assert res.status_code == 200, res.text
        assert res.json() == {"id": "row-1", "state": "saved"}
        assert (seen["org"], seen["member"]) == (ORG, "member@pn1.test")
        assert seen["sub"].expiration_time.year == 2030
        assert len(seen["sub"].user_agent) == routes.MAX_USER_AGENT

    def test_a_device_past_the_cap_gets_409(self, push_on, monkeypatch):
        async def save(*_a):
            raise store.TooManyDevices()

        monkeypatch.setattr(store, "save", save)
        res = _client().post("/push/subscriptions", json=_body())
        assert res.status_code == 409


# ── The payload ──────────────────────────────────────────────────────────────


class TestThePayload:
    def test_the_test_payload_is_the_shared_fixture_and_holds_no_content(self):
        fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        payload = json.loads(sender.TEST_MESSAGE.payload())
        assert payload == fixture
        assert set(payload) == {"title", "url", "tag"}
        assert payload["title"] == "Metorite test notification"
        assert "body" not in payload

    def test_a_body_and_a_tag_are_carried_when_a_caller_sets_them(self):
        m = sender.PushMessage(title="T", body="B", url="/chat?x=1", tag="r1", urgency="high")
        assert json.loads(m.payload()) == {"title": "T", "body": "B", "url": "/chat?x=1",
                                           "tag": "r1"}

    @pytest.mark.parametrize("url", ["https://evil.test/", "//evil.test/x", "chat"])
    def test_a_url_off_this_app_is_refused(self, url):
        with pytest.raises(ValueError):
            sender.PushMessage(title="T", url=url).payload()


# ── The send ─────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.reason = "x"
        self.text = ""


DEVICE = store.Device(id="d1", endpoint=ENDPOINT, **device_keys())


class TestTheSend:
    @pytest.mark.parametrize("status,outcome", [
        (404, "gone"), (410, "gone"), (400, "failed"), (403, "failed"),
        (413, "failed"), (429, "failed"), (500, "failed"), (307, "failed"),
    ])
    def test_an_answer_maps_to_its_outcome(self, push_on, monkeypatch, status, outcome):
        from pywebpush import WebPushException

        def fake(**_kw):
            raise WebPushException("x", response=_Response(status))

        monkeypatch.setattr("pywebpush.webpush", fake)
        assert sender._post(DEVICE, sender.TEST_MESSAGE, flags.vapid(), "mailto:a@b.test") == outcome

    def test_a_transport_fault_is_a_failure_and_never_raises(self, push_on, monkeypatch):
        def fake(**_kw):
            raise requests.ConnectionError("down")

        monkeypatch.setattr("pywebpush.webpush", fake)
        assert sender._post(DEVICE, sender.TEST_MESSAGE, flags.vapid(), "mailto:a@b.test") == "failed"

    def test_each_send_gets_its_own_claims_and_a_hardened_session(self, push_on, monkeypatch):
        calls = []

        def fake(**kw):
            calls.append(kw)
            kw["vapid_claims"]["aud"] = "https://fcm.googleapis.com"  # webpush writes it
            return _Response(201)

        monkeypatch.setattr("pywebpush.webpush", fake)
        for _ in range(2):
            assert sender._post(DEVICE, sender.TEST_MESSAGE, flags.vapid(), "mailto:a@b.test") == "sent"
        assert calls[0]["vapid_claims"] is not calls[1]["vapid_claims"]
        assert calls[1]["vapid_claims"] == {"sub": "mailto:a@b.test", "aud": "https://fcm.googleapis.com"}
        assert calls[0]["headers"] == {"Urgency": "normal"}
        assert calls[0]["ttl"] == 60 and calls[0]["timeout"] == sender.SEND_TIMEOUT_S
        session = calls[0]["requests_session"]
        assert isinstance(session, sender._NoRedirectSession)
        assert session.trust_env is False

    def test_the_session_never_follows_a_redirect(self, monkeypatch):
        seen = {}

        def base_request(self, method, url, *args, **kwargs):
            seen.update(kwargs)
            return _Response(201)

        monkeypatch.setattr(requests.Session, "request", base_request)
        sender._NoRedirectSession().post(ENDPOINT, data=b"x", allow_redirects=True)
        assert seen["allow_redirects"] is False

    async def test_send_refuses_without_configured_keys(self, push_on, monkeypatch):
        monkeypatch.setattr(get_settings(), "vapid_subject", "")
        monkeypatch.setattr(store, "devices", lambda *_a: pytest.fail("no read"))
        with pytest.raises(RuntimeError):
            await sender.send_to_member(ORG, EMAIL, sender.TEST_MESSAGE)


# ── The test route ───────────────────────────────────────────────────────────


class TestTheTestRoute:
    def test_it_sends_to_the_session_member_and_answers_the_counts(self, push_on, monkeypatch):
        seen = {}

        async def send(org, member, message):
            seen.update(org=org, member=member, message=message)
            return sender.SendReport(devices=2, sent=1, removed=1, failed=0)

        monkeypatch.setattr(sender, "send_to_member", send)
        res = _client().post("/push/test")
        assert res.status_code == 200
        assert res.json() == {"devices": 2, "sent": 1, "removed": 1, "failed": 0}
        assert (seen["org"], seen["member"], seen["message"]) == (ORG, EMAIL, sender.TEST_MESSAGE)
        assert set() == routes._IN_FLIGHT

    async def test_a_second_test_of_the_same_member_gets_429(self, push_on, monkeypatch):
        import asyncio

        gate = asyncio.Event()

        async def send(*_a):
            await gate.wait()
            return sender.SendReport(devices=0, sent=0, removed=0, failed=0)

        monkeypatch.setattr(sender, "send_to_member", send)
        user = UserContext(email=EMAIL, role=UserRole.EMPLOYEE,
                           access=build_access(["feature:chat"]), organization_id=ORG)
        first = asyncio.create_task(routes.push_test(user))
        await asyncio.sleep(0)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as refused:
            await routes.push_test(user)
        assert refused.value.status_code == 429
        gate.set()
        await first
        assert set() == routes._IN_FLIGHT
