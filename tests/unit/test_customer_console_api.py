"""The Customer Console HTTP surface, end to end against a real Postgres.

Spec: ``project-docs/specs/customer_console.md`` §6 CP-1/CP-2.

Covers the three things that decide whether this service is safe to run at all:
the door is shut by default, the seat cap actually refuses, and a retried usage
report does not bill twice.
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from tests.unit._customer_console_ladder import (  # noqa: E402
    DEFAULT_DEPLOYMENT_LABEL,
    apply_ladder,
    ensure_deployment,
)

_URL = os.environ.get("CUSTOMER_CONSOLE_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _URL,
    reason=(
        "CUSTOMER_CONSOLE_DATABASE_URL unset — R8 requires a REAL Postgres. "
        "A skip here is not a pass; CI must set it."
    ),
)

TOKEN = "test-operator-token"
INTERNAL = "test-internal-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
INT = {"Authorization": f"Bearer {INTERNAL}"}


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def deployment():
    """The box every organization here is provisioned onto.

    ``deployment_label`` is REQUIRED since WS-29 MT-1j slice 4 and resolves
    against a real row — the operator names the box and nothing infers it. This
    suite's subject is seats and metering, not placement, so one shared row is
    enough; it is ensured per test because a sibling fence empties the table.
    """
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        ensure_deployment(conn)
    eng.dispose()
    return DEFAULT_DEPLOYMENT_LABEL


@pytest.fixture
def org(client, deployment):
    slug = f"org-{uuid.uuid4().hex[:8]}"
    r = client.post("/orgs/provision", headers=AUTH, json={
        "slug": slug, "name": "Acme Pumps", "owner_email": f"owner@{slug}.com",
        "gstin": "29ABCDE1234F1Z5", "billing_state": "KA", "core_seats": 2,
        "deployment_label": deployment,
    })
    assert r.status_code == 200, r.text
    return slug


class TestTheDoor:
    def test_health_is_open_and_says_nothing(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_every_real_endpoint_refuses_without_a_token(self, client):
        assert client.get("/billing/summary?org_slug=x").status_code == 401
        assert client.post("/credits/grant", json={
            "org_slug": "x", "credits": "1"}).status_code == 401

    def test_a_wrong_token_is_refused(self, client):
        r = client.get("/billing/summary?org_slug=x",
                       headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_an_unconfigured_service_refuses_everything(self, monkeypatch):
        # Fails CLOSED, not open. This is D33.1's lesson applied from the first
        # line rather than retrofitted after a box was left public.
        monkeypatch.delenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", raising=False)
        from customer_console.main import app
        r = TestClient(app).get("/billing/summary?org_slug=x", headers=AUTH)
        assert r.status_code == 503


class TestProvisioning:
    def test_provisioning_twice_yields_one_org_and_one_grant(
        self, client, deployment
    ):
        slug = f"idem-{uuid.uuid4().hex[:8]}"
        body = {"slug": slug, "name": "N", "owner_email": "o@n.com",
                "core_seats": 5, "deployment_label": deployment}

        first = client.post("/orgs/provision", headers=AUTH, json=body).json()
        second = client.post("/orgs/provision", headers=AUTH, json=body).json()

        assert first["organization_id"] == second["organization_id"]

        # The half that costs money: a retry must not keep buying seats.
        summary = client.get(f"/billing/summary?org_slug={slug}",
                             headers=AUTH).json()
        core = next(s for s in summary["seats"] if s["plan_slug"] == "core")
        assert core["purchased"] == 5

    def test_the_owner_holds_a_core_seat(self, client, org):
        summary = client.get(f"/billing/summary?org_slug={org}",
                             headers=AUTH).json()
        core = next(s for s in summary["seats"] if s["plan_slug"] == "core")
        assert core["assigned"] == 1
        assert core["available"] == 1

    def test_an_unknown_org_is_404_not_500(self, client):
        assert client.get("/billing/summary?org_slug=nope",
                          headers=AUTH).status_code == 404


class TestSeatResolution:
    def test_a_member_signing_in_consumes_a_seat(self, client, org):
        r = client.post("/registry/resolve", headers=AUTH, json={
            "org_slug": org, "email": "staff@acme.com"})
        assert r.status_code == 200
        assert "core" in r.json()["seats"]

        summary = client.get(f"/billing/summary?org_slug={org}",
                             headers=AUTH).json()
        core = next(s for s in summary["seats"] if s["plan_slug"] == "core")
        assert core["assigned"] == 2

    def test_signing_in_repeatedly_does_not_burn_seats(self, client, org):
        # THE drift bug: resolve runs on EVERY sign-in.
        for _ in range(5):
            assert client.post("/registry/resolve", headers=AUTH, json={
                "org_slug": org, "email": "staff@acme.com"}).status_code == 200

        summary = client.get(f"/billing/summary?org_slug={org}",
                             headers=AUTH).json()
        core = next(s for s in summary["seats"] if s["plan_slug"] == "core")
        assert core["assigned"] == 2

    def test_the_cap_refuses_a_new_member_with_a_buy_more_payload(self, client, org):
        # Org has 2 core seats: owner + one staff member fills it.
        client.post("/registry/resolve", headers=AUTH,
                    json={"org_slug": org, "email": "staff@acme.com"})

        r = client.post("/registry/resolve", headers=AUTH,
                        json={"org_slug": org, "email": "third@acme.com"})

        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["reason"] == "seat_cap_exceeded"
        assert detail["buy_more"]["additional_seats_required"] == 1
        assert detail["buy_more"]["price_inr"] == "600.00"

    def test_releasing_a_seat_lets_the_next_member_in(self, client, org):
        client.post("/registry/resolve", headers=AUTH,
                    json={"org_slug": org, "email": "staff@acme.com"})
        assert client.post("/registry/resolve", headers=AUTH, json={
            "org_slug": org, "email": "third@acme.com"}).status_code == 409

        client.post("/billing/seats/release", headers=AUTH, json={
            "org_slug": org, "email": "staff@acme.com", "plan_slug": "core"})

        assert client.post("/registry/resolve", headers=AUTH, json={
            "org_slug": org, "email": "third@acme.com"}).status_code == 200


class TestCreditsOverHttp:
    def test_granting_credits_moves_the_balance(self, client, org):
        r = client.post("/credits/grant", headers=AUTH, json={
            "org_slug": org, "credits": "1000"})
        assert Decimal(r.json()["balance"]) == Decimal("1000")

    def test_usage_draws_down_and_a_replay_does_not(
        self, client, org, deployment
    ):
        client.post("/credits/grant", headers=AUTH,
                    json={"org_slug": org, "credits": "100"})

        # CP-3 (as revised after verification): the METER is written by the
        # Router's internal token, never by the customer's own key — the metered
        # party must not be the reporter of its own usage. Cross-org and
        # negative-value rejection are pinned in test_customer_console_key_auth.py.
        org_id = client.post("/orgs/provision", headers=AUTH, json={
            "slug": org, "name": "Acme Pumps",
            "owner_email": f"owner@{org}.com",
            "deployment_label": deployment}).json()["organization_id"]

        rid = f"req-{uuid.uuid4().hex}"
        body = {"organization_id": org_id, "request_id": rid,
                "billed_credits": "12.5", "model": "m", "tier": "tier-balanced"}

        first = client.post("/usage/record", headers=INT, json=body).json()
        assert first["recorded"] is True
        assert Decimal(first["balance"]) == Decimal("87.5")

        replay = client.post("/usage/record", headers=INT, json=body).json()
        assert replay["recorded"] is False
        assert Decimal(replay["balance"]) == Decimal("87.5")

    def test_a_trial_org_reports_its_status(self, client, org):
        r = client.get(f"/credits/balance?org_slug={org}", headers=AUTH).json()
        assert r["org_status"] == "trial"
