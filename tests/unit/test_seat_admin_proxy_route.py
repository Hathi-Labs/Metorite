"""WS-30 SC-2a — ``POST /seats/assign`` + ``/seats/release``: the gateway seat proxy.

Spec: ``project-docs/specs/subscription_console.md`` SC-2a (the gateway-tier
transport, done-whens 1/4/5) · ``customer_console.md`` §6 item (h) / §9 residual
7 · ``user_management_contract.md`` R11.

The middle hop of **browser → Next hop → gateway → Console**. This suite is the
gateway-side R7 fence for the transport, and it mirrors two established files:

* the ROUTE posture (auth-by-construction, the unwired refusal, R11 body-claim
  400s, and relaying the Console's verdict) is driven through a ``TestClient``
  mounted the way ``gateway/main.py`` mounts it — ``test_signin_resolve_route.py``'s
  idiom;
* the outbound WIRE (the deployment-key ``seat_admin`` arm: ``actor_email`` = the
  SESSION email, NO ``org_slug``, the bearer) is driven through an
  ``httpx.MockTransport`` Console — ``test_signup_provision_route.py``'s
  ``TestTheConsoleProvisionClient`` idiom.

⚠️ **DB-free on purpose, like ``test_signin_resolve_route.py`` and
``test_console_dependency_boundary.py``.** Nothing here opens a session: the
unwired path returns before any hop, and the wired path drives a MockTransport
Console — no real Console, no database. A DB gate leaking in would disarm this
fence silently, so it is named in ``pr-check.yml``'s no-database-fence grep.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx
from acb_auth import get_current_user
from acb_auth.console_resolve import ConsoleSeatWriteUnavailable
from acb_auth.deps import require_authenticated
from acb_auth.roles import UserContext, UserRole
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import seats as route

PERSON = UserContext(email="admin@customer.example", role=UserRole.EMPLOYEE)
ANON = UserContext(email=None, role=UserRole.EMPLOYEE)

CONSOLE_URL = "https://console.invalid"
DEPLOYMENT_KEY = "cc_depl_fixture_notarealsecret"

_ROOT = Path(__file__).resolve().parents[2]


def _client(user: UserContext = PERSON) -> TestClient:
    """The route mounted exactly as ``gateway/main.py`` mounts it."""
    app = FastAPI(dependencies=[require_authenticated(public=())])
    app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class Recorder:
    """Captures structured log calls, with the LEVEL, so a fence can assert it."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def warning(self, event: str, **kw) -> None:
        self.events.append(("warning", event, kw))

    def error(self, event: str, **kw) -> None:
        self.events.append(("error", event, kw))

    def info(self, event: str, **kw) -> None:
        self.events.append(("info", event, kw))

    def names(self) -> list[str]:
        return [name for _, name, _ in self.events]


class Spy:
    """Records whether the seat-write client was consulted, and with what."""

    def __init__(self, result=None, exc=None) -> None:
        self.result = result if result is not None else (200, {"assigned": True})
        self.exc = exc
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.fixture
def unwired(monkeypatch):
    """A gateway whose Console settings are genuinely absent (real is_wired)."""
    from acb_common.settings import get_settings

    monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
    monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def wired(monkeypatch):
    """A gateway told how to reach a Console (real is_wired, MockTransport hop)."""
    from acb_common.settings import get_settings

    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
    monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _mock_console(monkeypatch, handler):
    """Drive the ONE Console client through an httpx.MockTransport."""
    def _new_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("acb_auth.console_resolve._new_http_client", _new_client)


# ── The hand-list defends itself (no database; must always run) ──────────────

class TestThisFenceIsRegistered:
    """A no-database fence that silently skipped would disarm the transport's R7
    guard, so it is named in the CI no-database-fence grep and in the owning
    spec's verify block — the closest a hand-list gets to defending itself."""

    def test_named_in_the_ci_no_db_fence_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_seat_admin_proxy_route.py" in workflow

    def test_named_in_the_owning_spec_verify_block(self):
        spec = (_ROOT / "project-docs/specs/subscription_console.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_seat_admin_proxy_route.py" in spec


# ══ done-when 4 · the posture, and a WRITE must never silently succeed dark ═══

class TestThePosture:
    def test_an_anonymous_caller_never_reaches_the_handler(self, wired, monkeypatch):
        """Authenticated by construction, and NOT in ``PUBLIC_ROUTES``."""
        spy = Spy()
        monkeypatch.setattr(route, "assign_seat_on_console", spy)

        r = _client(ANON).post("/seats/assign", json={"member_email": "m@x.example",
                                                       "plan_slug": "center-ops"})

        assert r.status_code == 401
        assert spy.calls == []

    def test_an_unwired_box_refuses_the_write_before_the_hop(self, unwired, monkeypatch):
        """The F5 posture for a WRITE: an unwired box refuses with 503 rather
        than silently succeeding, and never consults the Console client.

        Mutation: drop the ``is_wired()`` guard and the write reaches the client
        (``spy.calls`` non-empty) on a box configured for no Console.
        """
        spy = Spy()
        monkeypatch.setattr(route, "assign_seat_on_console", spy)

        r = _client().post("/seats/assign", json={"member_email": "m@x.example",
                                                   "plan_slug": "center-ops"})

        assert r.status_code == 503
        assert spy.calls == [], "the route wrote on a box it cannot reach"

    def test_the_unwired_refusal_leaves_a_LOG_LINE_at_error(self, unwired, monkeypatch):
        """A silent dark write is exactly what F5 forbids — the refusal logs."""
        recorder = Recorder()
        monkeypatch.setattr(route, "_log", recorder)
        monkeypatch.setattr(route, "assign_seat_on_console", Spy())

        _client().post("/seats/assign", json={"member_email": "m@x.example",
                                              "plan_slug": "center-ops"})

        assert recorder.names().count("seats.write_unwired") == 1
        level, _, fields = recorder.events[0]
        assert level == "error"
        assert fields, "the line carries no detail an operator could act on"

    def test_release_on_an_unwired_box_also_refuses(self, unwired, monkeypatch):
        spy = Spy()
        monkeypatch.setattr(route, "release_seat_on_console", spy)

        r = _client().post("/seats/release", json={"member_email": "m@x.example",
                                                    "plan_slug": "center-ops"})

        assert r.status_code == 503
        assert spy.calls == []


# ══ R11 · the body may not claim a tenant or an actor ════════════════════════

class TestTheBodyClaims:
    @pytest.mark.parametrize("key", ["org", "org_slug", "actor_email", "email"])
    def test_a_body_that_claims_a_tenant_or_actor_is_400(self, wired, monkeypatch, key):
        """R11, refused on SHAPE before the client is called.

        Mutation: make the route trust ``raw['actor_email']`` (or ``org_slug``)
        and this 400 becomes a forwarded write with a browser-chosen actor/org.
        """
        spy = Spy()
        monkeypatch.setattr(route, "assign_seat_on_console", spy)

        r = _client().post(
            "/seats/assign",
            json={"member_email": "m@x.example", "plan_slug": "center-ops", key: "x"},
        )

        assert r.status_code == 400
        assert r.json()["code"] == "InvalidBody"
        assert spy.calls == []


# ══ done-when 1 · the outbound wire (deployment key + session actor, no org) ══

class TestTheOutboundWire:
    """The deployment-key ``seat_admin`` arm, driven through a MockTransport."""

    def _seen_for(self, monkeypatch, status=200, body=None):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(status, json=body or {"assigned": True})

        _mock_console(monkeypatch, handler)
        return seen

    def test_assign_forwards_the_deployment_key_and_the_session_actor_no_org(
        self, wired, monkeypatch
    ):
        """done-when 1: bearer = deployment key, ``actor_email`` = the SESSION
        email (never the browser's), ``member_email`` = the browser's target, and
        NO ``org_slug`` on the wire.

        Mutation: sourcing ``actor_email`` from the browser body, or attaching an
        ``org_slug``, fails the two asserts below.
        """
        seen = self._seen_for(monkeypatch)

        r = _client(PERSON).post(
            "/seats/assign",
            json={"member_email": "target@customer.example",
                  "plan_slug": "center-ops", "source": "alacarte"},
        )

        assert r.status_code == 200
        assert seen["url"].endswith("/registry/seats")
        assert seen["auth"] == f"Bearer {DEPLOYMENT_KEY}"
        # The acting admin is the SESSION email, not anything the browser sent.
        assert seen["body"]["actor_email"] == "admin@customer.example"
        assert seen["body"]["member_email"] == "target@customer.example"
        assert seen["body"]["plan_slug"] == "center-ops"
        assert seen["body"]["source"] == "alacarte"
        # R11 — the org is derived Console-side; the wire never names one.
        assert "org_slug" not in seen["body"]
        assert "org" not in seen["body"]

    def test_release_forwards_no_source_and_no_org(self, wired, monkeypatch):
        seen = self._seen_for(monkeypatch, body={"released": True})

        r = _client(PERSON).post(
            "/seats/release",
            json={"member_email": "target@customer.example", "plan_slug": "center-ops"},
        )

        assert r.status_code == 200
        assert seen["url"].endswith("/registry/seats/release")
        assert seen["auth"] == f"Bearer {DEPLOYMENT_KEY}"
        assert seen["body"]["actor_email"] == "admin@customer.example"
        assert "org_slug" not in seen["body"]
        assert "source" not in seen["body"]


# ══ done-when 5 · the Console's verdict surfaces through the relay ════════════

class TestTheRelay:
    def _console(self, monkeypatch, status, body):
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=body)
        _mock_console(monkeypatch, handler)

    def test_a_non_admin_403_surfaces_as_a_refusal(self, wired, monkeypatch):
        """done-when 5: the Console's ``seat_admin`` role gate 403s a non-admin
        actor, and the gateway relays that refusal rather than turning it into a
        200. Mutation: swallowing the status into a 200 admits the write."""
        self._console(
            monkeypatch, 403,
            {"detail": "the acting member is not an active admin of this organization"},
        )

        r = _client().post(
            "/seats/assign",
            json={"member_email": "target@customer.example", "plan_slug": "center-ops"},
        )

        assert r.status_code == 403
        assert "not an active admin" in json.dumps(r.json())

    def test_a_cap_409_surfaces_with_its_body(self, wired, monkeypatch):
        """The cap refusal (with ``buy_more``) relays verbatim — SC-2b renders it."""
        self._console(
            monkeypatch, 409,
            {"detail": {"reason": "no seats available", "buy_more": {"plan_slug": "center-ops"}}},
        )

        r = _client().post(
            "/seats/assign",
            json={"member_email": "target@customer.example", "plan_slug": "center-ops"},
        )

        assert r.status_code == 409
        assert r.json()["detail"]["buy_more"]["plan_slug"] == "center-ops"

    def test_a_happy_assign_relays_200(self, wired, monkeypatch):
        self._console(monkeypatch, 200, {"assigned": True, "plan_slug": "center-ops"})

        r = _client().post(
            "/seats/assign",
            json={"member_email": "target@customer.example", "plan_slug": "center-ops"},
        )

        assert r.status_code == 200
        assert r.json() == {"assigned": True, "plan_slug": "center-ops"}

    def test_a_console_5xx_becomes_a_503_not_a_leak(self, wired, monkeypatch):
        """A 5xx proves no answer — it degrades to a service-unavailable that
        names nothing about the customer, never the upstream's own words."""
        self._console(monkeypatch, 502, {"detail": "bad gateway from nginx"})

        r = _client().post(
            "/seats/assign",
            json={"member_email": "target@customer.example", "plan_slug": "center-ops"},
        )

        assert r.status_code == 503
        assert "nginx" not in json.dumps(r.json())


# ══ The Console seat-write CLIENT — the request it BUILDS (no route, no DB) ════

class TestTheSeatWriteClient:
    """``console_resolve.assign_seat_on_console`` / ``release_seat_on_console`` —
    the ONE Console client, driven with a MockTransport so the outbound request
    is the subject."""

    def _mock(self, monkeypatch, handler):
        _mock_console(monkeypatch, handler)

    async def test_assign_posts_the_deployment_key_arm_with_actor_and_no_org(
        self, wired, monkeypatch
    ):
        from acb_auth.console_resolve import assign_seat_on_console

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"assigned": True})

        self._mock(monkeypatch, handler)

        status, body = await assign_seat_on_console(
            actor_email="admin@customer.example",
            member_email="target@customer.example",
            plan_slug="center-ops",
            source="alacarte",
        )

        assert (status, body) == (200, {"assigned": True})
        assert seen["url"].endswith("/registry/seats")
        assert seen["auth"] == f"Bearer {DEPLOYMENT_KEY}"
        assert seen["body"] == {
            "member_email": "target@customer.example",
            "plan_slug": "center-ops",
            "actor_email": "admin@customer.example",
            "source": "alacarte",
        }
        assert "org_slug" not in seen["body"]

    async def test_release_omits_source(self, wired, monkeypatch):
        from acb_auth.console_resolve import release_seat_on_console

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"released": True})

        self._mock(monkeypatch, handler)

        status, body = await release_seat_on_console(
            actor_email="admin@customer.example",
            member_email="target@customer.example",
            plan_slug="center-ops",
        )

        assert (status, body) == (200, {"released": True})
        assert "source" not in seen["body"]
        assert "org_slug" not in seen["body"]

    async def test_a_403_is_RETURNED_not_raised(self, wired, monkeypatch):
        """A genuine verdict (403) is returned for the route to relay — only a
        no-answer status raises."""
        from acb_auth.console_resolve import assign_seat_on_console

        self._mock(monkeypatch, lambda req: httpx.Response(403, json={"detail": "no"}))

        status, _body = await assign_seat_on_console(
            actor_email="a@x.example", member_email="m@x.example",
            plan_slug="center-ops", source="alacarte",
        )
        assert status == 403

    async def test_a_5xx_becomes_ConsoleSeatWriteUnavailable(self, wired, monkeypatch):
        from acb_auth.console_resolve import assign_seat_on_console

        self._mock(monkeypatch, lambda req: httpx.Response(503))
        with pytest.raises(ConsoleSeatWriteUnavailable):
            await assign_seat_on_console(
                actor_email="a@x.example", member_email="m@x.example",
                plan_slug="center-ops", source="alacarte",
            )

    async def test_a_network_error_becomes_ConsoleSeatWriteUnavailable(
        self, wired, monkeypatch
    ):
        from acb_auth.console_resolve import assign_seat_on_console

        def handler(_req):
            raise httpx.ConnectError("refused")
        self._mock(monkeypatch, handler)
        with pytest.raises(ConsoleSeatWriteUnavailable):
            await assign_seat_on_console(
                actor_email="a@x.example", member_email="m@x.example",
                plan_slug="center-ops", source="alacarte",
            )

    async def test_an_unwired_box_raises_without_a_hop(self, monkeypatch):
        from acb_auth.console_resolve import assign_seat_on_console
        from acb_common.settings import get_settings

        monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
        monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
        get_settings.cache_clear()

        def _boom():
            raise AssertionError("no client should be built when unwired")
        monkeypatch.setattr("acb_auth.console_resolve._new_http_client", _boom)
        try:
            with pytest.raises(ConsoleSeatWriteUnavailable):
                await assign_seat_on_console(
                    actor_email="a@x.example", member_email="m@x.example",
                    plan_slug="center-ops", source="alacarte",
                )
        finally:
            get_settings.cache_clear()
