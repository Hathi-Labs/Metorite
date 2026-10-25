"""WS-66 PN-1, R8: ``push_subscriptions`` on a REAL Postgres.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §10.2, and
``chat_run_continuity.md`` §S7 items 6 and 7. The table is new, so a
hermetic fake would agree with any SQL it is handed. This runs the REAL
``gateway.push.store``, ``gateway.push.sender`` and the four route functions
on the phase-4-promoted catalog, as the NON-privileged role with RLS forced
(``_as_member``), as ``test_reach_r8.py`` does.

* **The table**: found by content, RLS forced, and the unique key per org.
* **The policy, two orgs**: a session bound to org B reads no row of org A,
  and WITH CHECK refuses a row stamped with another org.
* **The key per org**: org B saves the endpoint that org A holds.
* **Own rows only**: a member deletes only their own row of an endpoint. The
  sign-out path calls the same route.
* **The 404 and the 410**: the real ``pywebpush`` encrypts and signs. Only
  the push service is fake, at the ``requests`` session that ``webpush``
  posts through. A 404 or a 410 deletes the row, a 2xx stamps it, and a 500
  counts a failure. The payload on the wire decrypts to the fixture.

The hermetic half is ``tests/unit/test_push_platform.py``.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from gateway.push import routes, sender, store
from requests.structures import CaseInsensitiveDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# The catalog, the app role and the binding, shared rather than copied.
from tests.unit.test_email_keep_separate import (  # noqa: F401  (fixtures used by name)
    _DB_GATE,
    _as_member,
    app_engine,
    promoted,
)
from tests.unit.test_push_platform import _b64, vapid_pair

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
A = f"push-a-{TAG}@pn1.test"
B = f"push-b-{TAG}@pn1.test"
SUBJECT = "mailto:push@pn1.test"

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "push_test_payload.json"


def _endpoint(n: int | str) -> str:
    return f"https://fcm.googleapis.com/fcm/send/pn1-{TAG}-{n}"


class _Browser:
    """One browser's push keys. It keeps the private key, so a test can
    decrypt what the push service would carry to it."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"pn1-auth-secret!"[:16]
        point = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.p256dh = _b64(point)

    def sub(self, endpoint: str) -> store.Subscription:
        return store.Subscription(endpoint=endpoint, p256dh=self.p256dh, auth=_b64(self.auth))

    def body(self, endpoint: str) -> routes.SubscriptionBody:
        return routes.SubscriptionBody.model_validate({
            "endpoint": endpoint, "expirationTime": None,
            "keys": {"p256dh": self.p256dh, "auth": _b64(self.auth)}})

    def decrypt(self, data: bytes) -> dict:
        import http_ece

        return json.loads(http_ece.decrypt(data, private_key=self.key, auth_secret=self.auth,
                                           version="aes128gcm"))


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:chat"]), organization_id=org)


def _wipe(p) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM push_subscriptions WHERE member_email IN (:a, :b)"),
                  {"a": A, "b": B})


@pytest.fixture(autouse=True)
def _clean_rows(promoted):  # noqa: F811
    _wipe(promoted)
    yield
    _wipe(promoted)


@pytest.fixture
def push_on(monkeypatch):
    """The flag on, with an ephemeral VAPID pair. Never a real key."""
    private, public = vapid_pair()
    s = get_settings()
    monkeypatch.setattr(s, "web_push_enabled", True)
    monkeypatch.setattr(s, "vapid_private_key", private)
    monkeypatch.setattr(s, "vapid_public_key", public)
    monkeypatch.setattr(s, "vapid_subject", SUBJECT)


def _rows(p, member: str | None = None) -> list[dict]:
    with p.admin_engine.connect() as c:
        got = c.execute(text(
            "SELECT organization_id::text AS org, member_email, endpoint, p256dh, "
            "last_success_at, failure_count FROM push_subscriptions "
            "WHERE member_email IN (:a, :b) AND (CAST(:m AS text) IS NULL OR member_email = :m) "
            "ORDER BY created_at, endpoint"), {"a": A, "b": B, "m": member})
        return [dict(r._mapping) for r in got]


# ── The push service, faked under pywebpush ─────────────────────────────────


class _Answer:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.reason = "fake"
        self.text = ""
        self.headers = {}


class _PushService:
    """The push service: it answers each endpoint with the status it holds,
    and keeps every request that ``webpush`` posted to it."""

    def __init__(self, statuses: dict[str, int]) -> None:
        self.statuses = statuses
        self.posts: list[dict] = []

    def session(self) -> _PushService.Session:
        return _PushService.Session(self)

    class Session:
        def __init__(self, service: _PushService) -> None:
            self.service = service

        def post(self, url, data=None, headers=None, timeout=None, **kw):
            # webpush hands a CaseInsensitiveDict. Keep it so, as a real
            # HTTP server reads header names.
            self.service.posts.append({"url": url, "data": data,
                                       "headers": CaseInsensitiveDict(headers or {}),
                                       "timeout": timeout})
            return _Answer(self.service.statuses[url])

        def close(self) -> None:
            pass


@pytest.fixture
def service(monkeypatch):
    svc = _PushService({})
    monkeypatch.setattr(sender, "_session", svc.session)
    return svc


# ── The table ────────────────────────────────────────────────────────────────


class TestTheTable:
    def test_found_by_content_with_forced_rls(self, promoted):  # noqa: F811
        found = [f.name for f in _MIGRATIONS.glob("[0-9]*_*.sql")
                 if "CREATE TABLE IF NOT EXISTS push_subscriptions" in f.read_text(encoding="utf-8")]
        assert len(found) == 1, found
        with promoted.admin_engine.connect() as c:
            rls = c.execute(text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'push_subscriptions'::regclass")).one()
        assert tuple(rls) == (True, True)

    def test_the_unique_key_is_per_org_and_member_never_the_endpoint_alone(self, promoted):  # noqa: F811
        with promoted.admin_engine.connect() as c:
            keys = set(c.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'push_subscriptions'::regclass AND contype IN ('p', 'u')"
            )).scalars())
            unique_indexes = set(c.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE tablename = 'push_subscriptions' "
                "AND indexdef LIKE 'CREATE UNIQUE%'")).scalars())
        assert "UNIQUE (organization_id, member_email, endpoint)" in keys
        assert keys == {"PRIMARY KEY (id)", "UNIQUE (organization_id, member_email, endpoint)"}
        assert not [i for i in unique_indexes if i.rstrip().endswith("(endpoint)")]

    @pytest.mark.parametrize("column,value,check", [
        ("member_email", "Upper@pn1.test", "push_subscriptions_member_lower"),
        ("endpoint", "http://fcm.googleapis.com/x", "push_subscriptions_endpoint_https"),
    ])
    def test_the_checks_refuse_a_bad_row(self, promoted, column, value, check):  # noqa: F811
        row = {"member_email": A, "endpoint": _endpoint("check")}
        row[column] = value
        with pytest.raises(DBAPIError, match=check), promoted.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO push_subscriptions (organization_id, member_email, endpoint, "
                "p256dh, auth) VALUES (CAST(:o AS uuid), :m, :e, 'p', 'a')"),
                {"o": promoted.org_a, "m": row["member_email"], "e": row["endpoint"]})


# ── The policy, two orgs ─────────────────────────────────────────────────────


class TestThePolicy:
    async def test_org_b_reads_no_subscription_of_org_a(self, promoted, app_engine):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            await store.save(p.org_a, A, _Browser().sub(_endpoint(1)))
        # Raw SQL as the app role, with NO org predicate: only the policy filters.
        for org, want in ((p.org_b, 0), (p.org_a, 1)):
            with app_engine.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
                n = c.execute(text(
                    "SELECT count(*) FROM push_subscriptions WHERE member_email = :m"),
                    {"m": A}).scalar_one()
            assert n == want, (org, n)
        async with _as_member(p, p.org_b):
            assert await store.devices(p.org_b, A) == []
            assert not await store.remove(p.org_b, A, _endpoint(1))
        assert len(_rows(p, A)) == 1

    def test_with_check_refuses_a_row_stamped_with_another_org(self, promoted, app_engine):  # noqa: F811
        with pytest.raises(DBAPIError, match="row-level security"), app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                      {"o": promoted.org_a})
            c.execute(text(
                "INSERT INTO push_subscriptions (organization_id, member_email, endpoint, "
                "p256dh, auth) VALUES (CAST(:b AS uuid), :m, :e, 'p', 'a')"),
                {"b": promoted.org_b, "m": A, "e": _endpoint("forged")})

    async def test_the_org_column_comes_from_the_bound_tenant(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_b):
            await store.save(p.org_b, B, _Browser().sub(_endpoint(2)))
        assert [r["org"] for r in _rows(p, B)] == [p.org_b]


# ── The key per org, and own rows only ───────────────────────────────────────


class TestTheStore:
    async def test_org_b_saves_the_endpoint_that_org_a_holds(self, promoted):  # noqa: F811
        # One browser, two accounts of two orgs: the same endpoint. A key on
        # `endpoint` alone would make this insert fail on a row org B cannot see.
        p = promoted
        browser = _Browser()
        async with _as_member(p, p.org_a):
            await store.save(p.org_a, A, browser.sub(_endpoint("shared")))
        async with _as_member(p, p.org_b):
            await store.save(p.org_b, A, browser.sub(_endpoint("shared")))
            assert [d.endpoint for d in await store.devices(p.org_b, A)] == [_endpoint("shared")]
        assert sorted(r["org"] for r in _rows(p, A)) == sorted([p.org_a, p.org_b])

    async def test_a_second_save_refreshes_the_keys_of_the_one_row(self, promoted):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            first = await store.save(p.org_a, A, _Browser().sub(_endpoint(3)))
            fresh = _Browser()
            again = await store.save(p.org_a, A, fresh.sub(_endpoint(3)))
        assert first == again
        rows = _rows(p, A)
        assert len(rows) == 1 and rows[0]["p256dh"] == fresh.p256dh

    async def test_a_member_deletes_only_their_own_row(self, promoted):  # noqa: F811
        # Two members of one org on one browser hold one endpoint each.
        p = promoted
        browser = _Browser()
        async with _as_member(p, p.org_a):
            await store.save(p.org_a, A, browser.sub(_endpoint("one-browser")))
            await store.save(p.org_a, B, browser.sub(_endpoint("one-browser")))
            assert await store.remove(p.org_a, B, _endpoint("one-browser"))
            assert not await store.remove(p.org_a, B, _endpoint("one-browser"))
            assert [d.endpoint for d in await store.devices(p.org_a, A)] == [
                _endpoint("one-browser")]
            assert await store.devices(p.org_a, B) == []
        assert [r["member_email"] for r in _rows(p)] == [A]

    async def test_a_new_device_past_the_cap_is_refused(self, promoted):  # noqa: F811
        p = promoted
        browser = _Browser()
        async with _as_member(p, p.org_a):
            for n in range(store.MAX_DEVICES):
                await store.save(p.org_a, A, browser.sub(_endpoint(f"cap-{n}")))
            with pytest.raises(store.TooManyDevices):
                await store.save(p.org_a, A, browser.sub(_endpoint("cap-new")))
            # A device the member holds still refreshes at the cap.
            await store.save(p.org_a, A, browser.sub(_endpoint("cap-0")))
        assert len(_rows(p, A)) == store.MAX_DEVICES


# ── The routes, as the BFF calls them ────────────────────────────────────────


class TestTheRoutes:
    async def test_subscribe_then_the_sign_out_delete_removes_this_device(
            self, promoted, push_on):  # noqa: F811
        # The four sign-out paths call DELETE /push/subscriptions with this
        # device's endpoint while the session still names the member.
        p = promoted
        browser = _Browser()
        async with _as_member(p, p.org_a):
            saved = await routes.push_subscribe(browser.body(_endpoint("signout")), _user(p.org_a, A))
            await routes.push_subscribe(browser.body(_endpoint("other-device")), _user(p.org_a, A))
            assert saved["state"] == "saved"
            gone = await routes.push_unsubscribe(
                routes.EndpointBody(endpoint=_endpoint("signout")), _user(p.org_a, A))
            assert gone == {"removed": True}
        assert [r["endpoint"] for r in _rows(p, A)] == [_endpoint("other-device")]

    async def test_another_member_cannot_delete_my_device(self, promoted, push_on):  # noqa: F811
        p = promoted
        async with _as_member(p, p.org_a):
            await routes.push_subscribe(_Browser().body(_endpoint("mine")), _user(p.org_a, A))
            res = await routes.push_unsubscribe(
                routes.EndpointBody(endpoint=_endpoint("mine")), _user(p.org_a, B))
        assert res == {"removed": False}
        assert len(_rows(p, A)) == 1

    async def test_the_flag_off_writes_nothing(self, promoted, monkeypatch):  # noqa: F811
        monkeypatch.setattr(get_settings(), "web_push_enabled", False)
        p = promoted
        async with _as_member(p, p.org_a):
            # The router dependency answers 404 first. `_scope` checks the
            # flag again, so a direct call is closed too.
            with pytest.raises(HTTPException) as off:
                await routes.push_subscribe(_Browser().body(_endpoint("off")), _user(p.org_a, A))
        assert off.value.status_code == 404
        assert _rows(p, A) == []


# ── The sender: the 404, the 410 and a success ───────────────────────────────


class TestTheSender:
    async def test_a_410_and_a_404_delete_the_row_and_a_2xx_stamps_it(
            self, promoted, push_on, service):  # noqa: F811
        p = promoted
        browsers = {n: _Browser() for n in ("ok", "gone410", "gone404", "busy")}
        service.statuses.update({_endpoint("ok"): 201, _endpoint("gone410"): 410,
                                 _endpoint("gone404"): 404, _endpoint("busy"): 500})
        async with _as_member(p, p.org_a):
            for n, browser in browsers.items():
                await store.save(p.org_a, A, browser.sub(_endpoint(n)))
            report = await sender.send_to_member(p.org_a, A, sender.TEST_MESSAGE)
        assert (report.devices, report.sent, report.removed, report.failed) == (4, 1, 2, 1)
        rows = {r["endpoint"]: r for r in _rows(p, A)}
        assert set(rows) == {_endpoint("ok"), _endpoint("busy")}
        assert rows[_endpoint("ok")]["last_success_at"] is not None
        assert rows[_endpoint("ok")]["failure_count"] == 0
        assert rows[_endpoint("busy")]["last_success_at"] is None
        assert rows[_endpoint("busy")]["failure_count"] == 1

    async def test_the_wire_carries_the_signed_test_payload_and_no_content(
            self, promoted, push_on, service):  # noqa: F811
        p = promoted
        browser = _Browser()
        service.statuses[_endpoint("wire")] = 201
        async with _as_member(p, p.org_a):
            await store.save(p.org_a, A, browser.sub(_endpoint("wire")))
            await sender.send_to_member(p.org_a, A, sender.TEST_MESSAGE)
        (post,) = service.posts
        assert post["url"] == _endpoint("wire")
        assert post["headers"]["Authorization"].startswith("vapid t=")
        assert post["headers"]["Content-Encoding"] == "aes128gcm"
        assert post["headers"]["Urgency"] == "normal"
        assert post["headers"]["TTL"] == "60"
        payload = browser.decrypt(post["data"])
        assert payload == json.loads(_FIXTURE.read_text(encoding="utf-8"))
        assert "body" not in payload

    async def test_a_send_for_org_a_never_reaches_a_device_of_org_b(
            self, promoted, push_on, service):  # noqa: F811
        p = promoted
        service.statuses.update({_endpoint("a-dev"): 201, _endpoint("b-dev"): 410})
        async with _as_member(p, p.org_b):
            await store.save(p.org_b, A, _Browser().sub(_endpoint("b-dev")))
        async with _as_member(p, p.org_a):
            await store.save(p.org_a, A, _Browser().sub(_endpoint("a-dev")))
            report = await sender.send_to_member(p.org_a, A, sender.TEST_MESSAGE)
        assert [x["url"] for x in service.posts] == [_endpoint("a-dev")]
        assert (report.sent, report.removed) == (1, 0)
        assert {r["endpoint"] for r in _rows(p, A)} == {_endpoint("a-dev"), _endpoint("b-dev")}

    async def test_the_test_route_sends_to_my_devices_only(
            self, promoted, push_on, service):  # noqa: F811
        p = promoted
        service.statuses.update({_endpoint("mine"): 201, _endpoint("theirs"): 201})
        async with _as_member(p, p.org_a):
            await store.save(p.org_a, A, _Browser().sub(_endpoint("mine")))
            await store.save(p.org_a, B, _Browser().sub(_endpoint("theirs")))
            res = await routes.push_test(_user(p.org_a, A))
        assert res == {"devices": 1, "sent": 1, "removed": 0, "failed": 0}
        assert [x["url"] for x in service.posts] == [_endpoint("mine")]
