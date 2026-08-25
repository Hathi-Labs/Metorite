"""The gateway seat proxy: ``POST /seats/{assign,release}`` + ``GET /seats/overview``.

Spec: ``project-docs/specs/subscription_console.md`` SC-2a (the gateway-tier
transport, done-whens 1/4/5) · ``customer_console.md`` §6 item (h) / §9 residual
7 / **§6 CP-2h slice 1, D-SEAT-4** (the READ) · ``user_management_contract.md``
R11.

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


# ══ CP-2h slice 1 · GET /seats/overview — the D-SEAT-4 READ ═══════════════════
#
# The reroute this whole slice is: the customer Seats tab used to read
# `GET /me/seats` + `GET /me/members` through the workbench's per-org
# `CUSTOMER_CONSOLE_ORG_KEY`, which cannot be correct on a SHARED box (a
# `cc_live_` key IS one organization). The read now travels the same
# browser → Next hop → gateway → Console path the WRITES already take, on the
# per-BOX deployment key. This section is the gateway-tier half of that fence.

class TestTheOverviewPosture:
    def test_an_anonymous_caller_never_reaches_the_handler(self, wired, monkeypatch):
        """Authenticated by construction, and NOT in ``PUBLIC_ROUTES``.

        A read is not "harmless": this one returns an organization's whole
        roster, so an anonymous caller reaching the handler would be a
        cross-tenant disclosure, not a cosmetic bug.
        """
        spy = Spy(result=(200, {"plans": [], "members": []}))
        monkeypatch.setattr(route, "seat_overview_on_console", spy)

        r = _client(ANON).get("/seats/overview")

        assert r.status_code == 401
        assert spy.calls == []

    def test_an_unwired_box_refuses_before_the_hop(self, unwired, monkeypatch):
        """503, and the Console client is never consulted.

        503 rather than an empty 200 is what the surface's "not configured for
        this deployment" state keys on — an empty grid and an unreachable
        Console look identical otherwise, and only one of them means the admin
        should stop and read.

        Mutation: dropping the ``is_wired()`` guard reaches the client on a box
        configured for no Console (``spy.calls`` non-empty).
        """
        spy = Spy(result=(200, {"plans": [], "members": []}))
        monkeypatch.setattr(route, "seat_overview_on_console", spy)

        r = _client().get("/seats/overview")

        assert r.status_code == 503
        assert spy.calls == [], "the route called a Console it cannot reach"
        assert "temporarily unavailable" in json.dumps(r.json())

    def test_the_unwired_read_logs_under_its_OWN_event_at_warning(
        self, unwired, monkeypatch
    ):
        """The read's dark refusal is the ordinary ship-dark state, not the F5
        write alarm — so it logs ``seats.read_unwired`` at WARNING and leaves
        ``seats.write_unwired`` meaning writes alone.

        Mutation: reusing ``_unwired_refusal`` here makes every page load emit
        the ERROR line an operator greps for real dark writes.
        """
        recorder = Recorder()
        monkeypatch.setattr(route, "_log", recorder)
        monkeypatch.setattr(
            route, "seat_overview_on_console",
            Spy(result=(200, {"plans": [], "members": []})),
        )

        _client().get("/seats/overview")

        assert recorder.names() == ["seats.read_unwired"]
        level, _, fields = recorder.events[0]
        assert level == "warning"
        assert fields, "the line carries no detail an operator could act on"


class TestTheOverviewOutboundWire:
    """The deployment-key ``seat_admin`` READ arm, driven through MockTransport."""

    def test_it_forwards_the_deployment_key_and_the_SESSION_actor_only(
        self, wired, monkeypatch
    ):
        """R11: bearer = the deployment key, ``actor_email`` = the SESSION email,
        and the wire names NO organization and NO member.

        Mutation: sourcing the actor from anywhere but the authenticated context
        — a header, a query parameter — fails the actor assert; attaching an
        ``org_slug`` fails the R11 asserts (and the Console would 400 it).
        """
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"plans": [], "members": []})

        _mock_console(monkeypatch, handler)

        r = _client(PERSON).get("/seats/overview")

        assert r.status_code == 200
        assert seen["url"].endswith("/registry/seats/overview")
        assert seen["auth"] == f"Bearer {DEPLOYMENT_KEY}"
        assert seen["body"] == {"actor_email": "admin@customer.example"}
        # R11 — the org is derived Console-side; the wire never names one.
        assert "org_slug" not in seen["body"]
        assert "org" not in seen["body"]

    def test_a_caller_cannot_name_a_tenant_in_the_query_string(
        self, wired, monkeypatch
    ):
        """A GET has no body, so the tempting place to smuggle a tenant is the
        query string. The route reads none, so an ``?org_slug=`` is inert: the
        outbound wire is byte-identical to the plain call.

        This is R11 held by CONSTRUCTION — there is nothing to ignore — which is
        why the assertion is about the outbound request rather than a 400.
        """
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"plans": [], "members": []})

        _mock_console(monkeypatch, handler)

        r = _client(PERSON).get(
            "/seats/overview?org_slug=victim&actor_email=someone@else.example"
        )

        assert r.status_code == 200
        assert seen["body"] == {"actor_email": "admin@customer.example"}


class TestTheOverviewRelay:
    def _console(self, monkeypatch, status, body):
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=body)
        _mock_console(monkeypatch, handler)

    def test_a_happy_read_relays_the_grid_and_the_roster_verbatim(
        self, wired, monkeypatch
    ):
        """The ONE seat vocabulary crosses this hop untouched: the gateway
        recomputes nothing and reshapes nothing."""
        payload = {
            "plans": [{
                "plan_slug": "core", "purchased": 3, "assigned": 1,
                "available": 2, "oversubscribed": False,
            }],
            "members": [{
                "email": "priya@customer.example", "role": "member",
                "status": "active", "seats": ["core"],
            }],
        }
        self._console(monkeypatch, 200, payload)

        r = _client().get("/seats/overview")

        assert r.status_code == 200
        assert r.json() == payload

    def test_a_non_admin_403_surfaces_as_a_refusal(self, wired, monkeypatch):
        """The Console's role gate 403s a plain member and the gateway relays it
        rather than turning it into an empty 200 — which would render as "your
        organization has no seats" to somebody who simply may not look."""
        self._console(
            monkeypatch, 403,
            {"detail": "the acting member is not an active admin of this organization"},
        )

        r = _client().get("/seats/overview")

        assert r.status_code == 403
        assert "not an active admin" in json.dumps(r.json())

    def test_a_multi_org_409_surfaces_as_itself(self, wired, monkeypatch):
        """A member of two orgs on one box: the Console will not guess, and the
        gateway must not flatten that into an outage."""
        self._console(
            monkeypatch, 409,
            {"detail": "the acting member belongs to more than one organization"},
        )

        r = _client().get("/seats/overview")

        assert r.status_code == 409
        assert "more than one organization" in json.dumps(r.json())

    def test_a_console_5xx_becomes_a_503_not_a_leak(self, wired, monkeypatch):
        """A 5xx proves no answer — it degrades to a service-unavailable that
        names nothing about the customer or the upstream."""
        self._console(monkeypatch, 502, {"detail": "bad gateway from nginx"})

        r = _client().get("/seats/overview")

        assert r.status_code == 503
        assert "nginx" not in json.dumps(r.json())

    def test_a_network_error_becomes_a_503(self, wired, monkeypatch):
        def handler(_req):
            raise httpx.ConnectError("refused")
        _mock_console(monkeypatch, handler)

        r = _client().get("/seats/overview")

        assert r.status_code == 503


class TestTheSeatOverviewClient:
    """``console_resolve.seat_overview_on_console`` — the request it BUILDS."""

    async def test_it_posts_the_actor_alone_and_no_org(self, wired, monkeypatch):
        from acb_auth.console_resolve import seat_overview_on_console

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"plans": [], "members": []})

        _mock_console(monkeypatch, handler)

        status, body = await seat_overview_on_console(
            actor_email="admin@customer.example",
        )

        assert (status, body) == (200, {"plans": [], "members": []})
        assert seen["url"].endswith("/registry/seats/overview")
        assert seen["auth"] == f"Bearer {DEPLOYMENT_KEY}"
        assert seen["body"] == {"actor_email": "admin@customer.example"}

    async def test_a_403_is_RETURNED_not_raised(self, wired, monkeypatch):
        """A verdict is relayed; only a no-answer status raises — the same line
        the two write arms draw, from the same ``_post_seat_call``."""
        from acb_auth.console_resolve import seat_overview_on_console

        _mock_console(
            monkeypatch, lambda req: httpx.Response(403, json={"detail": "no"}),
        )

        status, _body = await seat_overview_on_console(
            actor_email="a@x.example",
        )
        assert status == 403

    async def test_a_5xx_becomes_ConsoleSeatWriteUnavailable(
        self, wired, monkeypatch
    ):
        from acb_auth.console_resolve import seat_overview_on_console

        _mock_console(monkeypatch, lambda req: httpx.Response(503))
        with pytest.raises(ConsoleSeatWriteUnavailable):
            await seat_overview_on_console(actor_email="a@x.example")

    async def test_an_unwired_box_raises_without_a_hop(self, monkeypatch):
        """Ship-dark one layer below the route, so a future caller that forgets
        the ``is_wired()`` guard still cannot reach a Console it has no key for.
        """
        from acb_auth.console_resolve import seat_overview_on_console
        from acb_common.settings import get_settings

        monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
        monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
        get_settings.cache_clear()

        def _boom():
            raise AssertionError("no client should be built when unwired")
        monkeypatch.setattr("acb_auth.console_resolve._new_http_client", _boom)
        try:
            with pytest.raises(ConsoleSeatWriteUnavailable):
                await seat_overview_on_console(actor_email="a@x.example")
        finally:
            get_settings.cache_clear()
