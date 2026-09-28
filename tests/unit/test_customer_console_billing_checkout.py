"""H-152, the checkout half — orders and redemption under the deployment key.

Spec: ``project-docs/HANDOFF.md`` H-152 · ``customer_console.md`` §6 CP-9
§9.3 (the checkout) and CP-2h (D-SEAT-4, the pattern) ·
``user_management_contract.md`` R11.

The org-key checkout (``POST /billing/orders``, ``GET /billing/orders/{id}``,
``POST /billing/orders/{id}/redeem``) presented ``CUSTOMER_CONSOLE_ORG_KEY``,
which names ONE tenant. The ``billing_purchase`` doors take the per-box
deployment key and derive the organization from the acting member.

What this suite pins, over a real database:

1. **One body.** The deployment door's order equals the org-key read of the
   same order, and creating it writes no entitlement row.
2. **Org A never touches org B.** A's order is a byte-identical 404 to B's
   owner, the same 404 as an unknown id — no membership oracle.
3. **Redeem stays idempotent**, and a code bound to another org is the ONE
   collapsed "no such discount code".
4. **The audit row names the member**, because this credential reaches one.
5. **The capability and R11 gates**, the operator refusal, and the lifecycle.
6. **The transitive fence:** the three doors reach no entitlement writer,
   except redeem → ``payments.fulfil``, the one edge the code licenses.

⚠️ **R8.** Skips loudly without a server; ``pr-check.yml`` names it.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_customer_console_billing_checkout.py
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from customer_console import payments
from customer_console.auth import (
    BILLING_PURCHASE_CAPABILITY,
    BILLING_READ_CAPABILITY,
    MEMBER_ADMIN_CAPABILITY,
    RESOLVE_CAPABILITY,
)
from customer_console.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.unit._customer_console_ladder import (
    SECOND_PLAN,
    apply_ladder,
    ensure_deployment,
    ensure_second_plan,
    mint_deployment_key,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

_ROOT = Path(__file__).resolve().parents[2]

TOKEN = "test-operator-token"
INTERNAL = "test-internal-token"
OP = {"Authorization": f"Bearer {TOKEN}"}

BOX_LABEL = "billing-checkout-suite-box"
ORDERS = "/registry/billing/orders"

#: The tables a checkout write must not touch, except through a redeemed code.
ENTITLEMENT_TABLES = ("org_subscription", "seat_grant", "seat_assignment", "credit_ledger")


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def db():
    return create_engine(_URL, future=True)


@pytest.fixture
def fake():
    provider = payments.FakeProvider()
    payments.set_provider(provider)
    yield provider
    payments.set_provider(None)


@pytest.fixture
def client(monkeypatch, fake):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _box(db):
    with db.begin() as c:
        ensure_deployment(c, label=BOX_LABEL)
        ensure_second_plan(c)


def _new_org(client) -> dict:
    slug = f"h152c-{uuid.uuid4().hex[:8]}"
    owner = f"owner@{slug}.example"
    r = client.post("/orgs/provision", headers=OP, json={
        "slug": slug, "name": "N", "owner_email": owner, "core_seats": 3,
        "billing_state": "KA", "deployment_label": BOX_LABEL,
    })
    assert r.status_code == 200, r.text
    return {"slug": slug, "owner": owner, "id": r.json()["organization_id"]}


def _key(db, capabilities: list[str], label: str = BOX_LABEL) -> str:
    with db.begin() as c:
        dep = str(c.execute(
            text("SELECT id FROM deployment WHERE label = :l"), {"l": label},
        ).scalar_one())
        return mint_deployment_key(c, deployment_id=dep, capabilities=capabilities)


@pytest.fixture
def buy_key(db):
    return _key(db, [
        RESOLVE_CAPABILITY, MEMBER_ADMIN_CAPABILITY, BILLING_PURCHASE_CAPABILITY,
    ])


def _post(client, key: str, path: str, **body):
    return client.post(path, headers={"Authorization": f"Bearer {key}"}, json=body)


def _basket(quantity: int = 2) -> list[dict]:
    return [{"plan_slug": SECOND_PLAN, "quantity": quantity}]


def _create(client, key: str, actor: str, **extra):
    return _post(client, key, ORDERS, actor_email=actor, lines=_basket(), **extra)


def _org_key(client, slug: str) -> str:
    r = client.post("/keys", headers=OP, json={"org_slug": slug})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _snapshot(db, org_id: str) -> dict[str, int]:
    with db.begin() as c:
        return {
            table: int(c.execute(
                text(f"SELECT count(*) FROM {table} WHERE organization_id = :o"),
                {"o": org_id},
            ).scalar_one())
            for table in ENTITLEMENT_TABLES
        }


def _issue_code(client, **body) -> str:
    payload = {"label": "h152", "kind": "percent", "percent_bp": 10000, **body}
    r = client.post("/discounts", headers=OP, json=payload)
    assert r.status_code == 200, r.text
    return r.json()["code"]


# ── The capability gate, R11 and the operator ────────────────────────────────

class TestTheGates:
    @pytest.mark.parametrize("suffix", ["", "/{id}", "/{id}/redeem"])
    def test_billing_read_does_not_open_the_checkout(self, client, db, suffix):
        """Reading the bill must not silently mean "may start a payment"."""
        org = _new_org(client)
        reader = _key(db, [BILLING_READ_CAPABILITY])
        path = ORDERS + suffix.replace("{id}", str(uuid.uuid4()))
        body = {"actor_email": org["owner"]}
        if suffix == "":
            body["lines"] = _basket()
        if suffix.endswith("redeem"):
            body["code"] = "cc_disc_x_y"
        r = client.post(path, headers={"Authorization": f"Bearer {reader}"}, json=body)
        assert r.status_code == 403, r.text
        assert BILLING_PURCHASE_CAPABILITY in r.json()["detail"]

    def test_the_operator_token_is_refused(self, client):
        org = _new_org(client)
        r = client.post(ORDERS, headers=OP,
                        json={"org_slug": org["slug"], "lines": _basket()})
        assert r.status_code == 403, r.text

    def test_naming_an_org_slug_is_400(self, client, buy_key):
        org = _new_org(client)
        r = _create(client, buy_key, org["owner"], org_slug=org["slug"])
        assert r.status_code == 400, r.text

    @pytest.mark.parametrize("field", ["total_paise", "price_paise", "amount"])
    def test_a_price_in_the_body_is_refused_never_used(self, client, buy_key, field):
        """``extra: forbid``: every paisa comes from the catalog (§9.2)."""
        org = _new_org(client)
        r = _create(client, buy_key, org["owner"], **{field: 1})
        assert r.status_code == 422, r.text

    def test_an_unknown_actor_is_the_byte_identical_403(self, client, buy_key):
        r = _create(client, buy_key, "ghost@nowhere.example")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == (
            "the acting member is not an admin on this deployment"
        )


# ── One body: the order the deployment door creates IS the twin's ────────────

class TestTheOrder:
    def test_creating_an_order_writes_no_entitlement_and_prices_from_the_catalog(
        self, client, db, buy_key
    ):
        org = _new_org(client)
        before = _snapshot(db, org["id"])
        r = _create(client, buy_key, org["owner"])
        assert r.status_code == 200, r.text
        assert _snapshot(db, org["id"]) == before
        order = r.json()
        assert order["status"] == "created"
        # 2 x Rs 600 = 120000 paise, + 18 percent GST — the catalog's price.
        assert (order["gross_paise"], order["gst_paise"], order["total_paise"]) == (
            120000, 21600, 141600,
        )

    def test_the_read_back_equals_the_org_key_twin(self, client, buy_key):
        org = _new_org(client)
        order = _create(client, buy_key, org["owner"]).json()
        live = _org_key(client, org["slug"])

        mine = client.get(f"/billing/orders/{order['id']}",
                          headers={"Authorization": f"Bearer {live}"})
        over = _post(client, buy_key, f"{ORDERS}/{order['id']}",
                     actor_email=org["owner"])
        assert over.status_code == 200, over.text
        assert over.json() == mine.json()

    def test_the_audit_row_names_the_member(self, client, db, buy_key):
        """The org key reached nobody, so its rows say "organization". This
        credential reaches a person, and the row says who."""
        org = _new_org(client)
        order = _create(client, buy_key, org["owner"]).json()
        with db.begin() as c:
            actor = c.execute(
                text("SELECT actor FROM control_audit WHERE organization_id = :o "
                     "AND action = 'order.create' AND detail->>'order_id' = :i"),
                {"o": org["id"], "i": order["id"]},
            ).scalar_one()
        assert actor == org["owner"]


# ── Org A never touches org B ────────────────────────────────────────────────

class TestCrossOrgIsolation:
    def test_a_foreign_order_is_the_same_404_as_an_unknown_one(
        self, client, buy_key
    ):
        a = _new_org(client)
        b = _new_org(client)
        order = _create(client, buy_key, a["owner"]).json()

        foreign = _post(client, buy_key, f"{ORDERS}/{order['id']}",
                        actor_email=b["owner"])
        unknown = _post(client, buy_key, f"{ORDERS}/{uuid.uuid4()}",
                        actor_email=b["owner"])
        assert foreign.status_code == unknown.status_code == 404
        assert foreign.content == unknown.content

    def test_redeeming_against_a_foreign_order_is_404_and_moves_nothing(
        self, client, db, buy_key
    ):
        a = _new_org(client)
        b = _new_org(client)
        order = _create(client, buy_key, a["owner"]).json()
        code = _issue_code(client)
        before = _snapshot(db, a["id"])

        r = _post(client, buy_key, f"{ORDERS}/{order['id']}/redeem",
                  actor_email=b["owner"], code=code)
        assert r.status_code == 404, r.text
        assert _snapshot(db, a["id"]) == before

    def test_a_code_bound_to_another_org_is_the_collapsed_refusal(
        self, client, buy_key
    ):
        a = _new_org(client)
        b = _new_org(client)
        order = _create(client, buy_key, a["owner"]).json()
        bound_to_b = _issue_code(client, org_slug=b["slug"])
        r = _post(client, buy_key, f"{ORDERS}/{order['id']}/redeem",
                  actor_email=a["owner"], code=bound_to_b)
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "no such discount code"


# ── Redemption ───────────────────────────────────────────────────────────────

class TestRedemption:
    def test_a_full_code_fulfils_and_a_second_presentation_is_idempotent(
        self, client, db, buy_key
    ):
        org = _new_org(client)
        order = _create(client, buy_key, org["owner"]).json()
        code = _issue_code(client, org_slug=org["slug"])

        first = _post(client, buy_key, f"{ORDERS}/{order['id']}/redeem",
                      actor_email=org["owner"], code=code)
        assert first.status_code == 200, first.text
        assert first.json()["total_paise"] == 0
        after_first = _snapshot(db, org["id"])

        again = _post(client, buy_key, f"{ORDERS}/{order['id']}/redeem",
                      actor_email=org["owner"], code=code)
        # A terminal order refuses a second act as a statement about the
        # ORDER — and nothing is granted twice either way.
        assert again.status_code in (200, 409), again.text
        assert _snapshot(db, org["id"]) == after_first
        with db.begin() as c:
            redemptions = c.execute(
                text("SELECT count(*) FROM discount_redemption WHERE order_id = :i"),
                {"i": order["id"]},
            ).scalar_one()
        assert redemptions == 1

    def test_a_partial_code_twice_on_an_open_order_redeems_once(
        self, client, db, buy_key
    ):
        org = _new_org(client)
        order = _create(client, buy_key, org["owner"]).json()
        code = _issue_code(client, org_slug=org["slug"], percent_bp=1000,
                           max_redemptions=5)
        first = _post(client, buy_key, f"{ORDERS}/{order['id']}/redeem",
                      actor_email=org["owner"], code=code)
        second = _post(client, buy_key, f"{ORDERS}/{order['id']}/redeem",
                       actor_email=org["owner"], code=code)
        assert first.status_code == second.status_code == 200, second.text
        assert first.json() == second.json()


# ── The lifecycle, and who the Console admits ────────────────────────────────

class TestTheLifecycle:
    def test_a_suspended_org_can_still_buy_its_way_out(self, client, buy_key):
        org = _new_org(client)
        r = client.post("/orgs/lifecycle", headers=OP,
                        json={"org_slug": org["slug"], "target": "suspended"})
        assert r.status_code == 200, r.text
        assert _create(client, buy_key, org["owner"]).status_code == 200

    def test_a_deleted_org_is_refused(self, client, buy_key):
        org = _new_org(client)
        for target in ("cancelled", "deleted"):
            assert client.post("/orgs/lifecycle", headers=OP, json={
                "org_slug": org["slug"], "target": target,
            }).status_code == 200
        assert _create(client, buy_key, org["owner"]).status_code == 403

    def test_an_active_plain_member_is_admitted_the_gateway_decides_who_buys(
        self, client, buy_key
    ):
        """WHO may spend is the tenant plane's ``billing:purchase``, checked
        at the gateway, as the workbench checked it for the org key. The
        Console asks for an active membership in the derived org."""
        org = _new_org(client)
        member = f"plain@{org['slug']}.example"
        assert _post(client, buy_key, "/registry/members",
                     member_email=member, actor_email=org["owner"]).status_code == 200
        assert _post(client, buy_key, "/registry/resolve",
                     email=member).status_code == 200
        assert _create(client, buy_key, member).status_code == 200

    def test_an_invited_member_who_never_signed_in_is_refused(
        self, client, buy_key
    ):
        org = _new_org(client)
        pending = f"pending@{org['slug']}.example"
        assert _post(client, buy_key, "/registry/members",
                     member_email=pending, actor_email=org["owner"]).status_code == 200
        assert _create(client, buy_key, pending).status_code == 403


# ── The transitive fence ─────────────────────────────────────────────────────

class TestTheTransitiveFence:
    DOORS = ("create_order_for_member", "read_order_for_member",
             "redeem_code_for_member")
    PERMITTED = frozenset({("redeem_code_for_member", "payments.fulfil")})

    def test_the_checkout_doors_reach_no_grant_writer_but_redeem(self):
        """CP-3's lesson, applied to the deployment-key checkout. The org-key
        fence walks only org-key routes, so these doors need their own walk.

        Mutation: pointing ``create_order_for_member`` at a seat grant turns
        this red with the path.
        """
        from tests.unit.test_customer_console_payments import _call_graph, _walk

        edges, writers = _call_graph()
        assert _walk(edges, writers, self.DOORS, self.PERMITTED) == []

    def test_the_walk_can_see_the_one_permitted_edge(self):
        """Non-vacuity: with nothing permitted, redeem DOES reach a writer."""
        from tests.unit.test_customer_console_payments import _call_graph, _walk

        edges, writers = _call_graph()
        found = _walk(edges, writers, self.DOORS, frozenset())
        assert {route for route, _ in found} == {"redeem_code_for_member"}


# ── The hand-lists defend themselves ─────────────────────────────────────────

class TestThisSuiteIsRegistered:
    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(
            encoding="utf-8")
        assert "tests/unit/test_customer_console_billing_checkout.py" in workflow

    def test_this_suite_is_named_in_the_owning_spec_verify_block(self):
        spec = (_ROOT / "project-docs/specs/customer_console.md").read_text(
            encoding="utf-8")
        assert "tests/unit/test_customer_console_billing_checkout.py" in spec
