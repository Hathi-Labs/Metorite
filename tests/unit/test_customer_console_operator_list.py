"""The Operator Console's cross-org customer list — ``GET /orgs`` (§4.1a, CP-8).

Spec: ``project-docs/specs/customer_console.md`` §4.1a / the CP-8 row · D35 ·
``saas_multitenancy.md`` §4.1a.

This is THE read that spans organizations (``saas_multitenancy.md`` §0.9.2), so
the two things that decide whether it is safe are (1) it is reachable **only**
under the ``Operator`` scheme — a customer's own ``cc_live_`` key, a wrong token
or no token is refused — and (2) the numbers it reports (seats, credit balance,
MRR) are the SAME ones every other surface computes, folded through the one seat
vocabulary and the one paise conversion rather than recomputed here.

R8 (root ``CLAUDE.md`` §3): the aggregate join, the effective-grant filter and
the credit ``SUM`` are exactly the class of SQL a hermetic fake agrees with and
a real Postgres does not, so this suite skips **loudly** when none is configured
and ``pr-check.yml``'s hand-list names it (``test_this_suite_is_named_in_the_ci_
skip_guard`` keeps that true from inside).

Run::

    export CUSTOMER_CONSOLE_DATABASE_URL=postgresql+psycopg://cc:cc@127.0.0.1:5442/cc_platform
    uv run pytest tests/unit/test_customer_console_operator_list.py
"""
from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from tests.unit._customer_console_ladder import (
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


@pytest.fixture(scope="module", autouse=True)
def _schema():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    eng.dispose()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("CUSTOMER_CONSOLE_INTERNAL_TOKEN", INTERNAL)
    from customer_console.main import app
    return TestClient(app)


@pytest.fixture
def deployment():
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        ensure_deployment(conn)
    eng.dispose()
    return DEFAULT_DEPLOYMENT_LABEL


def _provision(client, deployment, *, core_seats: int = 2) -> str:
    slug = f"opc-{uuid.uuid4().hex[:8]}"
    r = client.post("/orgs/provision", headers=AUTH, json={
        "slug": slug, "name": "Acme Pumps", "owner_email": f"owner@{slug}.com",
        "core_seats": core_seats, "deployment_label": deployment,
    })
    assert r.status_code == 200, r.text
    return slug


def _row_for(client, slug: str) -> dict:
    r = client.get("/orgs", headers=AUTH)
    assert r.status_code == 200, r.text
    rows = [o for o in r.json()["organizations"] if o["slug"] == slug]
    assert len(rows) == 1, f"{slug} appears {len(rows)} times"
    return rows[0]


class TestTheDoor:
    def test_the_list_refuses_without_a_token(self, client):
        assert client.get("/orgs").status_code == 401

    def test_a_wrong_token_is_refused(self, client):
        r = client.get("/orgs", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_a_customer_key_is_refused(self, client, deployment):
        # A cross-org read may NEVER be reached by a customer's own credential.
        # Mint a real cc_live_ organization key and present it: the operator
        # door takes the operator token alone, so a customer key is a 401 — the
        # structural statement that this surface is staff-only (D35.3).
        slug = _provision(client, deployment)
        token = client.post("/keys", headers=AUTH,
                            json={"org_slug": slug}).json()["token"]
        assert token.startswith("cc_live_")
        r = client.get("/orgs", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401

    def test_an_unconfigured_service_refuses(self, monkeypatch):
        # Fails CLOSED, not open (D33.1's lesson, from the first line).
        monkeypatch.delenv("CUSTOMER_CONSOLE_OPERATOR_TOKEN", raising=False)
        from customer_console.main import app
        assert TestClient(app).get("/orgs", headers=AUTH).status_code == 503


class TestTheList:
    def test_lists_every_org_cross_org(self, client, deployment):
        a = _provision(client, deployment)
        b = _provision(client, deployment)
        slugs = {o["slug"] for o in client.get("/orgs", headers=AUTH)
                 .json()["organizations"]}
        assert {a, b} <= slugs  # both, and the read spans organizations

    def test_a_fresh_org_is_trial_with_zero_mrr(self, client, deployment):
        # Provisioning creates a `trial` subscription; a trial is not revenue.
        slug = _provision(client, deployment)
        row = _row_for(client, slug)
        assert row["subscription_status"] == "trial"
        assert row["mrr_paise"] == 0
        assert row["status"] == "trial"          # the org lifecycle
        # The core seat grid is present and folded through the seat vocabulary.
        core = next(s for s in row["seats"] if s["plan_slug"] == "core")
        assert core["purchased"] == 2
        assert core["assigned"] == 1             # the owner holds one

    def test_an_active_subscription_reports_mrr_in_paise(
        self, client, deployment
    ):
        # core_seats=2 (₹600) + a manual activation of `sales` 3 seats (₹600):
        # MRR = (2 + 3) * ₹600 = ₹3000 = 300000 paise, and only because the
        # subscription is now `active`.
        slug = _provision(client, deployment, core_seats=2)
        r = client.post("/billing/subscriptions/activate", headers=AUTH, json={
            "org_slug": slug, "plan_slug": "sales", "seats": 3})
        assert r.status_code == 200, r.text

        row = _row_for(client, slug)
        assert row["subscription_status"] == "active"
        assert row["mrr_paise"] == 300000
        sales = next(s for s in row["seats"] if s["plan_slug"] == "sales")
        assert (sales["purchased"], sales["assigned"]) == (3, 0)

    def test_the_seat_grid_matches_billing_summary(self, client, deployment):
        # The ONE seat vocabulary (§3.3, D32.5): the cross-org list and the
        # per-org detail cannot report different purchased/assigned counts.
        slug = _provision(client, deployment, core_seats=3)
        client.post("/registry/resolve", headers=AUTH,
                    json={"org_slug": slug, "email": f"m@{slug}.com"})

        detail = client.get(f"/billing/summary?org_slug={slug}",
                            headers=AUTH).json()["seats"]
        listed = _row_for(client, slug)["seats"]

        def by_plan(rows):
            return {r["plan_slug"]: (r["purchased"], r["assigned"],
                                     r["available"], r["oversubscribed"])
                    for r in rows}

        assert by_plan(listed) == by_plan(detail)

    def test_credit_balance_reflects_grants(self, client, deployment):
        slug = _provision(client, deployment)
        client.post("/credits/grant", headers=AUTH,
                    json={"org_slug": slug, "credits": "1500"})
        row = _row_for(client, slug)
        assert Decimal(row["credit_balance"]) == Decimal("1500")

    def test_an_org_with_no_ledger_reads_zero_not_null(
        self, client, deployment
    ):
        # COALESCE(SUM(delta), 0): a never-granted org is a real 0, not null.
        slug = _provision(client, deployment)
        row = _row_for(client, slug)
        assert Decimal(row["credit_balance"]) == Decimal(0)

    def test_trial_expiry_and_provider_are_on_the_wire(
        self, client, deployment
    ):
        slug = _provision(client, deployment)
        row = _row_for(client, slug)
        # Keys are present (values may be null for a fresh org) — the surface
        # renders them, so their ABSENCE would be a silent hole.
        assert "trial_ends_at" in row
        assert "provider" in row
        assert "export_until" in row


class TestFences:
    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """The hand-list in pr-check.yml discovers nothing — a suite not named
        there skips silently without a database and leaves the job green."""
        workflow = (Path(__file__).resolve().parents[2] /
                    ".github/workflows/pr-check.yml").read_text(
                        encoding="utf-8")
        assert "tests/unit/test_customer_console_operator_list.py" in workflow

    def test_the_owning_spec_names_this_suite(self):
        """The §7 command block, in the same PR that creates the file."""
        spec = (Path(__file__).resolve().parents[2] /
                "project-docs/specs/customer_console.md").read_text(
                    encoding="utf-8")
        assert "test_customer_console_operator_list.py" in spec

    def test_only_the_operator_route_reaches_the_cross_org_read(self):
        """The cross-org read lives behind the Operator door and nowhere else.

        A source scan, not a request: every call to ``store.cross_org_summary``
        in the Console app must sit inside ``list_organizations``, whose only
        dependency is ``Operator``. If a second, unguarded caller ever appears,
        this fails before it ships.
        """
        main = (Path(__file__).resolve().parents[2] /
                "apps/services/customer_console/customer_console/main.py")
        body = main.read_text(encoding="utf-8")
        assert body.count("store.cross_org_summary(") == 1
        # The one caller is the Operator-gated list route.
        marker = "def list_organizations(_: Operator)"
        assert marker in body
        after = body.split(marker, 1)[1]
        # The call appears after the route signature, within the app.
        assert "store.cross_org_summary(" in after
