"""The gateway billing proxy: ``GET /billing/*`` under the deployment key.

Spec: ``project-docs/HANDOFF.md`` H-152 (the billing half) ·
``customer_console.md`` §6 CP-2h (D-SEAT-4, the pattern) ·
``user_management_contract.md`` R11 · D66.

The middle hop of **browser → Next hop → gateway → Console**, and its R7 fence.
The idioms are ``test_seat_admin_proxy_route.py``'s: the ROUTE through a
``TestClient`` mounted as ``gateway/main.py`` mounts it, and the outbound WIRE
through an ``httpx.MockTransport`` Console.

⚠️ **DB-free on purpose.** Nothing here opens a session. A DB gate leaking in
would disarm this fence silently, so ``pr-check.yml`` names it in the
no-database-fence grep.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx
from acb_auth import get_current_user
from acb_auth.console_resolve import (
    BILLING_READ_DOORS,
    CAPABILITY_REFUSAL_PREFIX,
    ConsoleBillingUnavailable,
)
from acb_auth.deps import require_authenticated
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import billing as route

ADMIN = UserContext(
    email="admin@customer.example",
    role=UserRole.EMPLOYEE,
    access=EffectiveAccess(role_granted=frozenset({"admin:members:read"})),
)
MEMBER = UserContext(
    email="member@customer.example",
    role=UserRole.EMPLOYEE,
    access=EffectiveAccess(role_granted=frozenset({"feature:settings"})),
)
ANON = UserContext(email=None, role=UserRole.EMPLOYEE)

CONSOLE_URL = "https://console.invalid"
DEPLOYMENT_KEY = "cc_depl_fixture_notarealsecret"

_ROOT = Path(__file__).resolve().parents[2]

#: Every GET this router serves, and the Console door it reaches.
ROUTES = {
    "/billing/summary": "/registry/billing/summary",
    "/billing/seats": "/registry/billing/seats",
    "/billing/members": "/registry/billing/members",
    "/billing/catalog": "/registry/billing/catalog",
    "/billing/usage/activity": "/registry/usage/activity",
    "/billing/usage/apps": "/registry/usage/apps",
    "/billing/usage/members": "/registry/usage/members",
}


def _client(user: UserContext = ADMIN) -> TestClient:
    app = FastAPI(dependencies=[require_authenticated(public=())])
    app.include_router(route.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class Recorder:
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
    def __init__(self, result=None) -> None:
        self.result = result if result is not None else (200, {})
        self.calls: list[tuple] = []

    async def __call__(self, door, **kwargs):
        self.calls.append((door, kwargs))
        return self.result


def _settings(monkeypatch, *, wired: bool):
    from acb_common.settings import get_settings

    if wired:
        monkeypatch.setenv("CUSTOMER_CONSOLE_URL", CONSOLE_URL)
        monkeypatch.setenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", DEPLOYMENT_KEY)
    else:
        monkeypatch.delenv("CUSTOMER_CONSOLE_URL", raising=False)
        monkeypatch.delenv("CUSTOMER_CONSOLE_DEPLOYMENT_KEY", raising=False)
    get_settings.cache_clear()


@pytest.fixture
def unwired(monkeypatch):
    from acb_common.settings import get_settings

    _settings(monkeypatch, wired=False)
    yield
    get_settings.cache_clear()


@pytest.fixture
def wired(monkeypatch):
    from acb_common.settings import get_settings

    _settings(monkeypatch, wired=True)
    yield
    get_settings.cache_clear()


def _mock_console(monkeypatch, handler):
    def _new_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("acb_auth.console_resolve._new_http_client", _new_client)


def _recording_console(monkeypatch, status=200, body=None) -> list[dict]:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({
            "url": str(request.url),
            "auth": request.headers.get("authorization"),
            "body": json.loads(request.content),
        })
        return httpx.Response(status, json=body if body is not None else {})

    _mock_console(monkeypatch, handler)
    return seen


# ── The hand-lists defend themselves ─────────────────────────────────────────

class TestThisFenceIsRegistered:
    def test_named_in_the_ci_no_db_fence_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_billing_proxy_route.py" in workflow
        assert "test_billing_proxy_route)" in workflow, (
            "named in the file list but not in the no-skip grep"
        )

    def test_the_router_is_mounted_by_the_gateway(self):
        src = (_ROOT / "apps/services/gateway/gateway/main.py").read_text(
            encoding="utf-8")
        assert "from gateway.routes.billing import router" in src

    def test_every_route_reaches_a_listed_door(self):
        assert sorted(ROUTES.values()) == sorted(BILLING_READ_DOORS.values())


# ── The posture ──────────────────────────────────────────────────────────────

class TestThePosture:
    @pytest.mark.parametrize("path", sorted(ROUTES))
    def test_an_anonymous_caller_never_reaches_the_handler(
        self, wired, monkeypatch, path
    ):
        spy = Spy()
        monkeypatch.setattr(route, "billing_read_on_console", spy)
        assert _client(ANON).get(path).status_code == 401
        assert spy.calls == []

    @pytest.mark.parametrize("path", sorted(ROUTES))
    def test_an_unwired_box_refuses_503_before_the_hop(
        self, unwired, monkeypatch, path
    ):
        """No Console, no answer — and no fallback to an organization key,
        because a fallback is how one tenant's figures reach another tenant."""
        spy = Spy()
        monkeypatch.setattr(route, "billing_read_on_console", spy)
        r = _client().get(path)
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]
        assert spy.calls == []

    def test_the_unwired_refusal_logs_at_warning(self, unwired, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(route, "_log", recorder)
        _client().get("/billing/summary")
        assert recorder.names() == ["billing.read_unwired"]
        assert recorder.events[0][0] == "warning"


# ── R11 · the outbound wire ──────────────────────────────────────────────────

class TestTheOutboundWire:
    @pytest.mark.parametrize("path,door", sorted(ROUTES.items()))
    def test_bearer_is_the_deployment_key_and_actor_is_the_session(
        self, wired, monkeypatch, path, door
    ):
        """Mutation: sourcing the actor from anywhere but the authenticated
        context, or attaching an ``org_slug``, fails these asserts."""
        seen = _recording_console(monkeypatch)
        r = _client(ADMIN).get(path)
        assert r.status_code == 200, r.text
        assert len(seen) == 1
        assert seen[0]["url"] == f"{CONSOLE_URL}{door}"
        assert seen[0]["auth"] == f"Bearer {DEPLOYMENT_KEY}"
        assert seen[0]["body"]["actor_email"] == "admin@customer.example"
        assert "org_slug" not in seen[0]["body"]
        assert "org" not in seen[0]["body"]
        assert set(seen[0]["body"]) <= {"actor_email", "scope"}

    @pytest.mark.parametrize("path", sorted(ROUTES))
    def test_the_query_string_cannot_name_a_tenant_or_a_person(
        self, wired, monkeypatch, path
    ):
        """R11 by construction: the routes read nothing from the request, so a
        smuggled ``org_slug`` or ``member`` changes nothing on the wire."""
        plain = _recording_console(monkeypatch)
        _client(ADMIN).get(path)
        smuggled = _recording_console(monkeypatch)
        _client(ADMIN).get(
            f"{path}?org_slug=victim&member=ceo@victim.example"
            "&actor_email=ceo@victim.example&scope=org"
        )
        assert smuggled[0]["body"] == plain[0]["body"]

    def test_only_the_two_spend_doors_carry_a_scope(self, wired, monkeypatch):
        seen = _recording_console(monkeypatch)
        for path in ROUTES:
            _client(ADMIN).get(path)
        scoped = {s["url"].removeprefix(CONSOLE_URL) for s in seen
                  if "scope" in s["body"]}
        assert scoped == {"/registry/usage/activity", "/registry/usage/apps"}


# ── The tenant plane's decision: who sees which spend ────────────────────────

class TestTheSpendScope:
    @pytest.mark.parametrize("path", ["/billing/usage/activity", "/billing/usage/apps"])
    def test_an_admin_reads_the_organization(self, wired, monkeypatch, path):
        seen = _recording_console(monkeypatch)
        _client(ADMIN).get(path)
        assert seen[0]["body"]["scope"] == "org"

    @pytest.mark.parametrize("path", ["/billing/usage/activity", "/billing/usage/apps"])
    def test_a_non_admin_reads_only_themselves(self, wired, monkeypatch, path):
        """Mutation: deciding the scope from anything but the resolved access
        set — or defaulting it to ``org`` — hands a member every colleague's
        spend."""
        seen = _recording_console(monkeypatch)
        _client(MEMBER).get(path)
        assert seen[0]["body"] == {
            "actor_email": "member@customer.example", "scope": "self",
        }

    def test_the_per_person_table_is_refused_to_a_non_admin_before_the_hop(
        self, wired, monkeypatch
    ):
        spy = Spy()
        monkeypatch.setattr(route, "billing_read_on_console", spy)
        r = _client(MEMBER).get("/billing/usage/members")
        assert r.status_code == 403
        assert "admins" in r.json()["detail"]
        assert spy.calls == []

    def test_the_per_person_table_reaches_the_console_for_an_admin(
        self, wired, monkeypatch
    ):
        seen = _recording_console(monkeypatch, body={"rows": [], "windowDays": 30})
        r = _client(ADMIN).get("/billing/usage/members")
        assert r.status_code == 200
        assert seen[0]["url"].endswith("/registry/usage/members")

    def test_no_resolved_access_is_not_an_admin(self, wired, monkeypatch):
        """A member whose access did not resolve holds ``NO_ACCESS``, and the
        safe reading of that is the narrow one."""
        bare = UserContext(email="bare@customer.example", role=UserRole.EMPLOYEE)
        seen = _recording_console(monkeypatch)
        _client(bare).get("/billing/usage/activity")
        assert seen[0]["body"]["scope"] == "self"
        assert _client(bare).get("/billing/usage/members").status_code == 403


# ── The relay ────────────────────────────────────────────────────────────────

class TestTheRelay:
    def _console(self, monkeypatch, status, body):
        _mock_console(monkeypatch, lambda _r: httpx.Response(status, json=body))

    def test_a_happy_read_is_relayed_verbatim(self, wired, monkeypatch):
        body = {"credits": {"balanceCredits": 12.5}, "invoices": []}
        self._console(monkeypatch, 200, body)
        r = _client().get("/billing/summary")
        assert (r.status_code, r.json()) == (200, body)

    def test_a_key_without_the_capability_is_NOT_CONFIGURED_not_forbidden(
        self, wired, monkeypatch
    ):
        """The box's key lacks ``billing_read``. That is this deployment's
        configuration, and the member must not be told they may not look.

        Mutation: relaying the 403 renders "you do not have access" to every
        member of every tenant until an operator widens the key.
        """
        recorder = Recorder()
        monkeypatch.setattr(route, "_log", recorder)
        self._console(monkeypatch, 403, {
            "detail": f"{CAPABILITY_REFUSAL_PREFIX}'billing_read' capability",
        })
        r = _client().get("/billing/summary")
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]
        assert "billing.capability_missing" in recorder.names()

    def test_a_not_a_member_403_is_relayed_as_itself(self, wired, monkeypatch):
        self._console(monkeypatch, 403, {
            "detail": "the acting member is not an admin on this deployment",
        })
        r = _client().get("/billing/summary")
        assert r.status_code == 403

    def test_a_multi_org_409_is_relayed_as_itself(self, wired, monkeypatch):
        self._console(monkeypatch, 409, {"detail": "more than one organization"})
        assert _client().get("/billing/seats").status_code == 409

    def test_a_console_5xx_is_a_503_that_names_nothing(self, wired, monkeypatch):
        self._console(monkeypatch, 502, {"detail": "bad gateway from nginx"})
        r = _client().get("/billing/summary")
        assert r.status_code == 503
        assert "nginx" not in r.text

    def test_a_console_401_is_a_503_never_a_sign_in_prompt(
        self, wired, monkeypatch
    ):
        """A 401 is this box's own key, never a fact about the member."""
        self._console(monkeypatch, 401, {"detail": "Invalid API key"})
        assert _client().get("/billing/summary").status_code == 503

    def test_a_network_error_is_a_503(self, wired, monkeypatch):
        def handler(_req):
            raise httpx.ConnectError("refused")
        _mock_console(monkeypatch, handler)
        assert _client().get("/billing/summary").status_code == 503


# ── The client ───────────────────────────────────────────────────────────────

class TestTheBillingReadClient:
    async def test_an_unknown_door_is_refused_before_any_hop(self, wired, monkeypatch):
        from acb_auth.console_resolve import billing_read_on_console

        def _boom():
            raise AssertionError("no client should be built for an unknown door")
        monkeypatch.setattr("acb_auth.console_resolve._new_http_client", _boom)
        with pytest.raises(KeyError):
            await billing_read_on_console("orders", actor_email="a@x.example")

    async def test_an_unwired_box_raises_without_a_hop(self, unwired, monkeypatch):
        from acb_auth.console_resolve import billing_read_on_console

        def _boom():
            raise AssertionError("no client should be built when unwired")
        monkeypatch.setattr("acb_auth.console_resolve._new_http_client", _boom)
        with pytest.raises(ConsoleBillingUnavailable):
            await billing_read_on_console("summary", actor_email="a@x.example")

    async def test_a_scope_that_is_not_org_is_sent_as_self(self, wired, monkeypatch):
        from acb_auth.console_resolve import billing_read_on_console

        seen = _recording_console(monkeypatch)
        await billing_read_on_console(
            "usage_apps", actor_email="a@x.example", scope="everyone",
        )
        assert seen[0]["body"]["scope"] == "self"


# ── The prefix the relay reads is the Console's own words ────────────────────

def test_the_console_still_opens_its_capability_refusal_with_the_prefix():
    """Hermetic twin of the R8 check in ``test_customer_console_billing_reads``.

    Reads the Console's ``auth.py`` for the f-string ``deployment_or_operator``
    raises. If that sentence were reworded, the relay above would stop seeing a
    missing capability and would tell members they may not look.
    """
    src = (_ROOT / "apps/services/customer_console/customer_console/auth.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    heads = [
        node.values[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        and node.values
        and isinstance(node.values[0], ast.Constant)
    ]
    assert CAPABILITY_REFUSAL_PREFIX in heads


# ══ The checkout (H-152, the checkout half) ═══════════════════════════════════

PURCHASER = UserContext(
    email="buyer@customer.example",
    role=UserRole.EMPLOYEE,
    access=EffectiveAccess(role_granted=frozenset({"billing:purchase"})),
)

ORDER_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"
CHECKOUT = [
    ("post", "/billing/orders", {"lines": [{"plan_slug": "core", "quantity": 1}]}),
    ("get", f"/billing/orders/{ORDER_ID}", None),
    ("post", f"/billing/orders/{ORDER_ID}/redeem", {"code": "cc_disc_abc_secret"}),
]


def _call(client: TestClient, method: str, path: str, body):
    if method == "get":
        return client.get(path)
    return client.post(path, json=body)


class CheckoutSpy:
    """Stands in for all three checkout clients and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def install(self, monkeypatch) -> None:
        for name in ("create_order_on_console", "read_order_on_console",
                     "redeem_code_on_console"):
            monkeypatch.setattr(route, name, self._fn(name))

    def _fn(self, name):
        async def _inner(**kwargs):
            self.calls.append((name, kwargs))
            return 200, {"id": ORDER_ID}
        return _inner


class TestTheCheckoutGate:
    @pytest.mark.parametrize("method,path,body", CHECKOUT)
    def test_an_anonymous_caller_reaches_nothing(
        self, wired, monkeypatch, method, path, body
    ):
        spy = CheckoutSpy()
        spy.install(monkeypatch)
        assert _call(_client(ANON), method, path, body).status_code == 401
        assert spy.calls == []

    @pytest.mark.parametrize("method,path,body", CHECKOUT)
    def test_a_member_without_billing_purchase_is_403_before_the_hop(
        self, wired, monkeypatch, method, path, body
    ):
        """The tenant plane's `billing:purchase`, kept exactly as the workbench
        applied it. An ADMIN without it is refused too: reading the org and
        spending its money are different grants.

        Mutation: dropping the gate lets any member start a payment.
        """
        spy = CheckoutSpy()
        spy.install(monkeypatch)
        for user in (MEMBER, ADMIN):
            r = _call(_client(user), method, path, body)
            assert r.status_code == 403
            assert "permission to make purchases" in r.json()["detail"]
        assert spy.calls == []

    @pytest.mark.parametrize("method,path,body", CHECKOUT)
    def test_a_purchaser_reaches_the_console(
        self, wired, monkeypatch, method, path, body
    ):
        spy = CheckoutSpy()
        spy.install(monkeypatch)
        r = _call(_client(PURCHASER), method, path, body)
        assert r.status_code == 200
        assert len(spy.calls) == 1
        assert spy.calls[0][1]["actor_email"] == "buyer@customer.example"

    @pytest.mark.parametrize("method,path,body", CHECKOUT)
    def test_an_unwired_box_refuses_503_before_the_hop(
        self, unwired, monkeypatch, method, path, body
    ):
        spy = CheckoutSpy()
        spy.install(monkeypatch)
        r = _call(_client(PURCHASER), method, path, body)
        assert r.status_code == 503
        assert spy.calls == []

    def test_an_unwired_WRITE_logs_at_error(self, unwired, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(route, "_log", recorder)
        _client(PURCHASER).post(
            "/billing/orders", json={"lines": [{"plan_slug": "core", "quantity": 1}]}
        )
        assert recorder.events[0][:2] == ("error", "billing.write_unwired")


class TestTheCheckoutBody:
    @pytest.mark.parametrize("key", ["org", "org_slug", "actor_email", "email"])
    @pytest.mark.parametrize(
        "path", ["/billing/orders", f"/billing/orders/{ORDER_ID}/redeem"]
    )
    def test_a_body_that_claims_a_tenant_or_actor_is_400(
        self, wired, monkeypatch, key, path
    ):
        spy = CheckoutSpy()
        spy.install(monkeypatch)
        body = {"lines": [{"plan_slug": "core", "quantity": 1}], "code": "c", key: "x"}
        r = _client(PURCHASER).post(path, json=body)
        assert r.status_code == 400
        assert r.json()["code"] == "InvalidBody"
        assert spy.calls == []

    def test_the_basket_is_rebuilt_and_carries_no_price(self, wired, monkeypatch):
        """The browser names a slug and a quantity. Everything else — a price
        it happens to display, a total — is dropped before the hop."""
        seen = _recording_console(monkeypatch, body={"id": ORDER_ID})
        r = _client(PURCHASER).post("/billing/orders", json={
            "lines": [{"plan_slug": "core", "quantity": 2, "price_paise": 1,
                       "unit_price_paise": 1}],
            "total_paise": 1,
        })
        assert r.status_code == 200
        assert seen[0]["url"] == f"{CONSOLE_URL}/registry/billing/orders"
        assert seen[0]["auth"] == f"Bearer {DEPLOYMENT_KEY}"
        assert seen[0]["body"] == {
            "actor_email": "buyer@customer.example",
            "lines": [{"plan_slug": "core", "quantity": 2}],
        }

    def test_an_empty_basket_is_400_before_the_hop(self, wired, monkeypatch):
        spy = CheckoutSpy()
        spy.install(monkeypatch)
        r = _client(PURCHASER).post("/billing/orders", json={"lines": []})
        assert r.status_code == 400
        assert spy.calls == []

    def test_an_empty_code_is_400_before_the_hop(self, wired, monkeypatch):
        spy = CheckoutSpy()
        spy.install(monkeypatch)
        r = _client(PURCHASER).post(
            f"/billing/orders/{ORDER_ID}/redeem", json={"code": "  "}
        )
        assert r.status_code == 400
        assert spy.calls == []

    def test_the_redeem_wire_carries_the_actor_and_the_code_only(
        self, wired, monkeypatch
    ):
        seen = _recording_console(monkeypatch, body={"id": ORDER_ID})
        _client(PURCHASER).post(
            f"/billing/orders/{ORDER_ID}/redeem", json={"code": " cc_disc_abc_secret "}
        )
        assert seen[0]["url"] == (
            f"{CONSOLE_URL}/registry/billing/orders/{ORDER_ID}/redeem"
        )
        assert seen[0]["body"] == {
            "actor_email": "buyer@customer.example", "code": "cc_disc_abc_secret",
        }

    async def test_the_order_id_is_quoted_into_the_path(self, wired, monkeypatch):
        """An order id is not a tenant, and it cannot walk to another door."""
        from acb_auth.console_resolve import read_order_on_console

        seen = _recording_console(
            monkeypatch, status=404, body={"detail": "no such order"}
        )
        status, _ = await read_order_on_console(
            actor_email="a@x.example", order_id="a/../b"
        )
        assert status == 404
        assert seen[0]["url"] == f"{CONSOLE_URL}/registry/billing/orders/a%2F..%2Fb"

    def test_the_code_is_never_logged_or_echoed(self, wired, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(route, "_log", recorder)
        _mock_console(monkeypatch, lambda _r: httpx.Response(422, json={
            "detail": [{"input": "cc_disc_abc_secret"}]}))
        r = _client(PURCHASER).post(
            f"/billing/orders/{ORDER_ID}/redeem", json={"code": "cc_disc_abc_secret"}
        )
        assert r.status_code == 400
        assert "cc_disc_abc_secret" not in json.dumps(recorder.events)
        assert "cc_disc_abc_secret" not in r.text


class TestTheCheckoutRelay:
    def _console(self, monkeypatch, status, body):
        _mock_console(monkeypatch, lambda _r: httpx.Response(status, json=body))

    @pytest.mark.parametrize("status,body", [
        (409, {"detail": {"reason": "expired"}}),
        (404, {"detail": "no such discount code"}),
        (404, {"detail": "no such order"}),
    ])
    def test_the_refusal_partition_is_relayed_verbatim(
        self, wired, monkeypatch, status, body
    ):
        """SC-4g's reasons and the one collapsed 404 reach the page as the
        Console wrote them."""
        self._console(monkeypatch, status, body)
        r = _client(PURCHASER).post(
            f"/billing/orders/{ORDER_ID}/redeem", json={"code": "cc_disc_abc_secret"}
        )
        assert (r.status_code, r.json()) == (status, body)

    def test_a_key_without_billing_purchase_is_not_configured(
        self, wired, monkeypatch
    ):
        self._console(monkeypatch, 403, {
            "detail": f"{CAPABILITY_REFUSAL_PREFIX}'billing_purchase' capability",
        })
        r = _client(PURCHASER).post(
            "/billing/orders", json={"lines": [{"plan_slug": "core", "quantity": 1}]}
        )
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]

    def test_an_unconfigured_provider_503_names_nothing(self, wired, monkeypatch):
        """The Console's 503 names the missing Razorpay variables: this
        deployment's configuration, not the customer's business."""
        self._console(monkeypatch, 503, {"detail": "RAZORPAY_KEY_ID is unset"})
        r = _client(PURCHASER).post(
            "/billing/orders", json={"lines": [{"plan_slug": "core", "quantity": 1}]}
        )
        assert r.status_code == 503
        assert "RAZORPAY" not in r.text

    def test_a_console_401_is_never_a_sign_in_prompt(self, wired, monkeypatch):
        self._console(monkeypatch, 401, {"detail": "Invalid API key"})
        r = _client(PURCHASER).get(f"/billing/orders/{ORDER_ID}")
        assert r.status_code == 503
